from __future__ import annotations

import json
import sqlite3
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import Bookmark, ClassificationResult


CURRENT_SCHEMA_VERSION = 9

LOW_CONFIDENCE_REVIEW_THRESHOLD = 0.6


@dataclass(frozen=True)
class ImportResult:
    total_seen: int
    unique_seen: int
    inserted: int
    updated: int
    unchanged: int
    duplicates: int

    @property
    def imported(self) -> int:
        return self.inserted + self.updated + self.unchanged


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    tweet_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    text TEXT NOT NULL,
    author TEXT,
    created_at TEXT,
    raw_json TEXT NOT NULL,
    content_hash TEXT,
    category TEXT,
    category_source TEXT NOT NULL DEFAULT 'auto',
    tags_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    reason TEXT,
    classification_provider TEXT,
    notes TEXT,
    read_state TEXT NOT NULL DEFAULT 'unread',
    important INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    review_state TEXT NOT NULL DEFAULT 'pending',
    review_reason TEXT,
    reviewed_at TEXT,
    export_path TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    change_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_state (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    input_path TEXT,
    archive_dir TEXT,
    connector TEXT,
    cursor_before TEXT,
    cursor_after TEXT,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    has_more INTEGER NOT NULL DEFAULT 0,
    provider TEXT,
    model TEXT,
    imported_count INTEGER NOT NULL DEFAULT 0,
    classified_count INTEGER NOT NULL DEFAULT 0,
    exported_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
    tweet_id UNINDEXED,
    text,
    author,
    url,
    category,
    tags,
    notes
);
"""


class BookmarkStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(BASE_SCHEMA)
            self._migrate(conn)

    def schema_version(self) -> int:
        self.init()
        with self.connect() as conn:
            return self._schema_version(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = self._schema_version(conn)
        if version < 1:
            self._record_migration(conn, 1)
            version = 1
        if version < 2:
            self._migrate_to_v2(conn)
            self._record_migration(conn, 2)
            version = 2
        if version < 3:
            self._migrate_to_v3(conn)
            self._record_migration(conn, 3)
            version = 3
        if version < 4:
            self._migrate_to_v4(conn)
            self._record_migration(conn, 4)
            version = 4
        if version < 5:
            self._migrate_to_v5(conn)
            self._record_migration(conn, 5)
            version = 5
        if version < 6:
            self._migrate_to_v6(conn)
            self._record_migration(conn, 6)
            version = 6
        if version < 7:
            self._migrate_to_v7(conn)
            self._record_migration(conn, 7)
            version = 7
        if version < 8:
            self._migrate_to_v8(conn)
            self._record_migration(conn, 8)
            version = 8
        if version < 9:
            self._migrate_to_v9(conn)
            self._record_migration(conn, 9)

    def _migrate_to_v2(self, conn: sqlite3.Connection) -> None:
        columns = self._bookmark_columns(conn)
        if "category_source" not in columns:
            conn.execute(
                "ALTER TABLE bookmarks "
                "ADD COLUMN category_source TEXT NOT NULL DEFAULT 'auto'"
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_category
            ON bookmarks(category)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_category_source
            ON bookmarks(category_source)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_created_at
            ON bookmarks(created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_export_path
            ON bookmarks(export_path)
            """
        )

    def _migrate_to_v3(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT,
                input_path TEXT,
                archive_dir TEXT,
                provider TEXT,
                model TEXT,
                imported_count INTEGER NOT NULL DEFAULT 0,
                classified_count INTEGER NOT NULL DEFAULT 0,
                exported_count INTEGER NOT NULL DEFAULT 0,
                message TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_run_logs_started_at
            ON run_logs(started_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_run_logs_status
            ON run_logs(status)
            """
        )

    def _migrate_to_v4(self, conn: sqlite3.Connection) -> None:
        columns = self._bookmark_columns(conn)
        if "content_hash" not in columns:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN content_hash TEXT")
        if "first_seen_at" not in columns:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN first_seen_at TEXT")
        if "last_seen_at" not in columns:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN last_seen_at TEXT")
        if "change_count" not in columns:
            conn.execute(
                "ALTER TABLE bookmarks "
                "ADD COLUMN change_count INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """
            UPDATE bookmarks
            SET first_seen_at = COALESCE(first_seen_at, imported_at, CURRENT_TIMESTAMP),
                last_seen_at = COALESCE(last_seen_at, updated_at, imported_at, CURRENT_TIMESTAMP)
            WHERE first_seen_at IS NULL OR last_seen_at IS NULL
            """
        )
        rows = conn.execute(
            """
            SELECT tweet_id, url, text, author, created_at, raw_json
            FROM bookmarks
            WHERE content_hash IS NULL OR content_hash = ''
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE bookmarks SET content_hash = ? WHERE tweet_id = ?",
                (
                    _bookmark_hash(
                        tweet_id=str(row["tweet_id"]),
                        url=str(row["url"]),
                        text=str(row["text"]),
                        author=row["author"],
                        created_at=row["created_at"],
                        raw_json=str(row["raw_json"]),
                    ),
                    row["tweet_id"],
                ),
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_content_hash
            ON bookmarks(content_hash)
            """
        )

    def _migrate_to_v5(self, conn: sqlite3.Connection) -> None:
        columns = self._bookmark_columns(conn)
        if "notes" not in columns:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN notes TEXT")
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
                tweet_id UNINDEXED,
                text,
                author,
                url,
                category,
                tags,
                notes
            )
            """
        )
        self._rebuild_search_index(conn)

    def _migrate_to_v6(self, conn: sqlite3.Connection) -> None:
        columns = self._bookmark_columns(conn)
        if "read_state" not in columns:
            conn.execute(
                "ALTER TABLE bookmarks "
                "ADD COLUMN read_state TEXT NOT NULL DEFAULT 'unread'"
            )
        if "important" not in columns:
            conn.execute(
                "ALTER TABLE bookmarks "
                "ADD COLUMN important INTEGER NOT NULL DEFAULT 0"
            )
        if "archived" not in columns:
            conn.execute(
                "ALTER TABLE bookmarks "
                "ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_read_state
            ON bookmarks(read_state)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_important
            ON bookmarks(important)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_archived
            ON bookmarks(archived)
            """
        )

    def _migrate_to_v7(self, conn: sqlite3.Connection) -> None:
        columns = self._run_log_columns(conn)
        migrations = {
            "connector": "TEXT",
            "cursor_before": "TEXT",
            "cursor_after": "TEXT",
            "pages_fetched": "INTEGER NOT NULL DEFAULT 0",
            "source_count": "INTEGER NOT NULL DEFAULT 0",
            "inserted_count": "INTEGER NOT NULL DEFAULT 0",
            "updated_count": "INTEGER NOT NULL DEFAULT 0",
            "unchanged_count": "INTEGER NOT NULL DEFAULT 0",
            "duplicate_count": "INTEGER NOT NULL DEFAULT 0",
            "has_more": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in migrations.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE run_logs ADD COLUMN {name} {definition}")

    def _migrate_to_v8(self, conn: sqlite3.Connection) -> None:
        columns = self._bookmark_columns(conn)
        if "classification_provider" not in columns:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN classification_provider TEXT")

    def _migrate_to_v9(self, conn: sqlite3.Connection) -> None:
        columns = self._bookmark_columns(conn)
        if "review_state" not in columns:
            conn.execute(
                "ALTER TABLE bookmarks "
                "ADD COLUMN review_state TEXT NOT NULL DEFAULT 'accepted'"
            )
        if "review_reason" not in columns:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN review_reason TEXT")
        if "reviewed_at" not in columns:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN reviewed_at TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bookmarks_review_state
            ON bookmarks(review_state)
            """
        )

    def _schema_version(self, conn: sqlite3.Connection) -> int:
        exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()
        if exists is None:
            return 0
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return int(row[0])

    def _record_migration(self, conn: sqlite3.Connection, version: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            (version,),
        )

    def _bookmark_columns(self, conn: sqlite3.Connection) -> set[str]:
        return {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(bookmarks)").fetchall()
        }

    def _run_log_columns(self, conn: sqlite3.Connection) -> set[str]:
        return {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(run_logs)").fetchall()
        }

    def upsert_bookmarks(self, bookmarks: Iterable[Bookmark]) -> ImportResult:
        self.init()
        by_id: dict[str, Bookmark] = {}
        total_seen = 0
        for bookmark in bookmarks:
            total_seen += 1
            by_id[bookmark.tweet_id] = bookmark

        inserted = 0
        updated = 0
        unchanged = 0
        with self.connect() as conn:
            for bookmark in by_id.values():
                raw_json = json.dumps(bookmark.raw or {}, ensure_ascii=False)
                content_hash = _bookmark_hash(
                    tweet_id=bookmark.tweet_id,
                    url=bookmark.url,
                    text=bookmark.text,
                    author=bookmark.author,
                    created_at=bookmark.created_at,
                    raw_json=raw_json,
                )
                current = conn.execute(
                    """
                    SELECT content_hash, category_source
                    FROM bookmarks
                    WHERE tweet_id = ?
                    """,
                    (bookmark.tweet_id,),
                ).fetchone()
                if current is None:
                    conn.execute(
                        """
                        INSERT INTO bookmarks (
                            tweet_id, url, text, author, created_at, raw_json,
                            content_hash, review_state, review_reason, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'new-import', CURRENT_TIMESTAMP)
                        """,
                        (
                            bookmark.tweet_id,
                            bookmark.url,
                            bookmark.text,
                            bookmark.author,
                            bookmark.created_at,
                            raw_json,
                            content_hash,
                        ),
                    )
                    self._sync_search_index(conn, bookmark.tweet_id)
                    inserted += 1
                    continue

                if str(current["content_hash"] or "") == content_hash:
                    conn.execute(
                        """
                        UPDATE bookmarks
                        SET last_seen_at = CURRENT_TIMESTAMP
                        WHERE tweet_id = ?
                        """,
                        (bookmark.tweet_id,),
                    )
                    self._sync_search_index(conn, bookmark.tweet_id)
                    unchanged += 1
                    continue

                if str(current["category_source"]) == "manual":
                    classification_sql = ""
                else:
                    classification_sql = """
                        category = NULL,
                        tags_json = '[]',
                        confidence = NULL,
                        reason = NULL,
                        classification_provider = NULL,
                        review_state = 'pending',
                        review_reason = 'content-changed',
                        reviewed_at = NULL,
                    """
                conn.execute(
                    f"""
                    UPDATE bookmarks
                    SET url = ?, text = ?, author = ?, created_at = ?,
                        raw_json = ?, content_hash = ?,
                        {classification_sql}
                        last_seen_at = CURRENT_TIMESTAMP,
                        change_count = change_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tweet_id = ?
                    """,
                    (
                        bookmark.url,
                        bookmark.text,
                        bookmark.author,
                        bookmark.created_at,
                        raw_json,
                        content_hash,
                        bookmark.tweet_id,
                    ),
                )
                self._sync_search_index(conn, bookmark.tweet_id)
                updated += 1
        unique_seen = len(by_id)
        return ImportResult(
            total_seen=total_seen,
            unique_seen=unique_seen,
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            duplicates=total_seen - unique_seen,
        )

    def iter_bookmarks(
        self,
        only_unclassified: bool = False,
        category: str | None = None,
        limit: int | None = None,
        skip_manual: bool = False,
    ) -> list[dict]:
        self.init()
        clauses = []
        params = []
        if only_unclassified:
            clauses.append("category IS NULL")
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        if skip_manual:
            clauses.append("category_source != 'manual'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = "LIMIT ?" if limit is not None else ""
        if limit is not None:
            params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT tweet_id, url, text, author, created_at, raw_json, content_hash,
                       change_count, first_seen_at, last_seen_at, category,
                       category_source, tags_json, confidence, reason,
                       classification_provider, notes,
                       read_state, important, archived, review_state,
                       review_reason, reviewed_at, export_path
                FROM bookmarks
                {where}
                ORDER BY COALESCE(created_at, imported_at) DESC, tweet_id DESC
                {limit_sql}
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_classification(
        self,
        tweet_id: str,
        result: ClassificationResult,
        source: str = "auto",
        provider: str | None = None,
    ) -> None:
        with self.connect() as conn:
            review_assignments = []
            if source == "manual":
                review_assignments = [
                    "review_state = 'accepted'",
                    "review_reason = NULL",
                    "reviewed_at = CURRENT_TIMESTAMP",
                ]
            elif (
                result.confidence is None
                or result.confidence < LOW_CONFIDENCE_REVIEW_THRESHOLD
            ):
                review_assignments = [
                    "review_state = 'pending'",
                    "review_reason = 'low-confidence'",
                    "reviewed_at = NULL",
                ]
            review_sql = (
                ", " + ", ".join(review_assignments) if review_assignments else ""
            )
            cursor = conn.execute(
                """
                UPDATE bookmarks
                SET category = ?, category_source = ?, tags_json = ?,
                    confidence = ?, reason = ?, classification_provider = ?,
                    updated_at = CURRENT_TIMESTAMP
                    {review_sql}
                WHERE tweet_id = ?
                """.format(review_sql=review_sql),
                (
                    result.category,
                    source,
                    json.dumps(result.tags, ensure_ascii=False),
                    result.confidence,
                    result.reason,
                    provider,
                    tweet_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Bookmark not found: {tweet_id}")
            self._sync_search_index(conn, tweet_id)

    def update_bookmark(
        self,
        tweet_id: str,
        *,
        category: str | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
        read_state: str | None = None,
        important: bool | None = None,
        archived: bool | None = None,
        review_state: str | None = None,
    ) -> None:
        self.init()
        assignments = []
        params: list[object] = []
        if category is not None:
            assignments.extend(
                [
                    "category = ?",
                    "category_source = 'manual'",
                    "confidence = 1.0",
                    "reason = 'Manually adjusted by user.'",
                    "classification_provider = 'manual'",
                    "review_state = 'accepted'",
                    "review_reason = NULL",
                    "reviewed_at = CURRENT_TIMESTAMP",
                ]
            )
            params.append(category.strip() or None)
        if tags is not None:
            assignments.append("tags_json = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if notes is not None:
            assignments.append("notes = ?")
            params.append(notes)
        if read_state is not None:
            if read_state not in {"read", "unread"}:
                raise ValueError("read_state must be read or unread")
            assignments.append("read_state = ?")
            params.append(read_state)
        if important is not None:
            assignments.append("important = ?")
            params.append(1 if important else 0)
        if archived is not None:
            assignments.append("archived = ?")
            params.append(1 if archived else 0)
        if review_state is not None:
            if review_state not in {"pending", "accepted"}:
                raise ValueError("review_state must be pending or accepted")
            assignments.append("review_state = ?")
            params.append(review_state)
            if review_state == "accepted":
                assignments.extend(
                    ["review_reason = NULL", "reviewed_at = CURRENT_TIMESTAMP"]
                )
            else:
                assignments.extend(
                    ["review_reason = 'manual-pending'", "reviewed_at = NULL"]
                )
        if not assignments:
            return
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        params.append(tweet_id)

        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE bookmarks
                SET {', '.join(assignments)}
                WHERE tweet_id = ?
                """,
                params,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Bookmark not found: {tweet_id}")
            self._sync_search_index(conn, tweet_id)

    def save_export_path(self, tweet_id: str, export_path: Path) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE bookmarks
                SET export_path = ?, updated_at = CURRENT_TIMESTAMP
                WHERE tweet_id = ?
                """,
                (str(export_path), tweet_id),
            )

    def save_notes(self, tweet_id: str, notes: str) -> None:
        self.init()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE bookmarks
                SET notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE tweet_id = ?
                """,
                (notes, tweet_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Bookmark not found: {tweet_id}")
            self._sync_search_index(conn, tweet_id)

    def search_bookmarks(self, query: str, limit: int = 20) -> list[dict]:
        self.init()
        if limit < 1:
            raise ValueError("limit must be at least 1")
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT b.tweet_id, b.url, b.text, b.author, b.created_at,
                       b.category, b.category_source, b.tags_json, b.confidence,
                       b.classification_provider, b.notes,
                       bm25(bookmarks_fts) AS rank
                FROM bookmarks_fts
                JOIN bookmarks AS b ON b.tweet_id = bookmarks_fts.tweet_id
                WHERE bookmarks_fts MATCH ?
                ORDER BY rank, COALESCE(b.created_at, b.imported_at) DESC,
                         b.tweet_id DESC
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_bookmark(self, tweet_id: str) -> dict | None:
        self.init()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT b.tweet_id, b.url, b.text, b.author, b.created_at,
                       b.category, b.category_source, b.tags_json, b.confidence,
                       b.reason, b.classification_provider, b.notes,
                       b.read_state, b.important, b.archived, b.review_state,
                       b.review_reason, b.reviewed_at, b.export_path, b.raw_json,
                       b.updated_at
                FROM bookmarks AS b
                WHERE b.tweet_id = ?
                """,
                (tweet_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_bookmarks(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        self.init()
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if offset < 0:
            raise ValueError("offset must be at least 0")

        clauses = []
        params: list[object] = []
        if category:
            if category == "Unclassified":
                clauses.append("b.category IS NULL")
            else:
                clauses.append("b.category = ?")
                params.append(category)
        if status:
            if status == "active":
                clauses.append("b.archived = 0")
            elif status == "archived":
                clauses.append("b.archived = 1")
            elif status == "important":
                clauses.append("b.important = 1")
            elif status in {"read", "unread"}:
                clauses.append("b.read_state = ?")
                params.append(status)
            elif status == "pending_review":
                clauses.append("b.review_state = 'pending'")
            else:
                raise ValueError(f"Unsupported status filter: {status}")

        fts_query = _fts_query(query or "")
        if fts_query:
            clauses.append("bookmarks_fts MATCH ?")
            params.append(fts_query)
            from_sql = """
                bookmarks_fts
                JOIN bookmarks AS b ON b.tweet_id = bookmarks_fts.tweet_id
            """
            rank_sql = "bm25(bookmarks_fts) AS rank,"
            order_sql = (
                "rank, "
                "CASE WHEN b.tweet_id GLOB '[0-9]*' THEN CAST(b.tweet_id AS INTEGER) END DESC, "
                "COALESCE(b.created_at, b.imported_at) DESC, b.tweet_id DESC"
            )
        else:
            from_sql = "bookmarks AS b"
            rank_sql = ""
            order_sql = (
                "CASE WHEN b.tweet_id GLOB '[0-9]*' THEN CAST(b.tweet_id AS INTEGER) END DESC, "
                "COALESCE(b.created_at, b.imported_at) DESC, b.tweet_id DESC"
            )

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT b.tweet_id, b.url, b.text, b.author, b.created_at,
                       b.category, b.category_source, b.tags_json, b.confidence,
                       b.reason, b.classification_provider, b.notes,
                       b.read_state, b.important, b.archived, b.review_state,
                       b.review_reason, b.reviewed_at, b.export_path, b.raw_json,
                       {rank_sql} b.updated_at
                FROM {from_sql}
                {where}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def set_sync_state(self, name: str, value: str) -> None:
        self.init()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (name, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (name, value),
            )

    def sync_state(self) -> dict[str, str]:
        self.init()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name, value FROM sync_state ORDER BY name"
            ).fetchall()
        return {str(row["name"]): str(row["value"]) for row in rows}

    def begin_run(
        self,
        command: str,
        input_path: Path | None = None,
        archive_dir: Path | None = None,
        connector: str | None = None,
        cursor_before: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> int:
        self.init()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO run_logs (
                    command, status, input_path, archive_dir, connector,
                    cursor_before, provider, model
                )
                VALUES (?, 'running', ?, ?, ?, ?, ?, ?)
                """,
                (
                    command,
                    str(input_path) if input_path else None,
                    str(archive_dir) if archive_dir else None,
                    connector,
                    cursor_before,
                    provider,
                    model,
                ),
            )
            run_id = int(cursor.lastrowid)
            self._set_sync_state(conn, "current_run_id", str(run_id))
            self._set_sync_state(conn, "last_run_status", "running")
            self._set_sync_state(conn, "last_run_command", command)
        return run_id

    def finish_run(
        self,
        run_id: int,
        status: str,
        imported_count: int = 0,
        classified_count: int = 0,
        exported_count: int = 0,
        cursor_after: str | None = None,
        pages_fetched: int = 0,
        source_count: int = 0,
        inserted_count: int = 0,
        updated_count: int = 0,
        unchanged_count: int = 0,
        duplicate_count: int = 0,
        has_more: bool = False,
        message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE run_logs
                SET status = ?, ended_at = CURRENT_TIMESTAMP,
                    imported_count = ?, classified_count = ?, exported_count = ?,
                    cursor_after = ?, pages_fetched = ?, source_count = ?,
                    inserted_count = ?, updated_count = ?, unchanged_count = ?,
                    duplicate_count = ?, has_more = ?,
                    message = ?
                WHERE id = ?
                """,
                (
                    status,
                    imported_count,
                    classified_count,
                    exported_count,
                    cursor_after,
                    pages_fetched,
                    source_count,
                    inserted_count,
                    updated_count,
                    unchanged_count,
                    duplicate_count,
                    1 if has_more else 0,
                    message,
                    run_id,
                ),
            )
            self._set_sync_state(conn, "current_run_id", "")
            self._set_sync_state(conn, "last_run_id", str(run_id))
            self._set_sync_state(conn, "last_run_status", status)
            if message:
                self._set_sync_state(conn, "last_run_message", message)

    def list_run_logs(self, limit: int = 10) -> list[dict]:
        self.init()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, command, status, started_at, ended_at, input_path,
                       archive_dir, connector, cursor_before, cursor_after,
                       pages_fetched, source_count, inserted_count,
                       updated_count, unchanged_count, duplicate_count,
                       has_more, provider, model, imported_count,
                       classified_count, exported_count, message
                FROM run_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        self.init()
        with self.connect() as conn:
            read = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE read_state = 'read'"
            ).fetchone()[0]
            unread = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE read_state = 'unread'"
            ).fetchone()[0]
            important = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE important = 1"
            ).fetchone()[0]
            archived = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE archived = 1"
            ).fetchone()[0]
            pending_review = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE review_state = 'pending'"
            ).fetchone()[0]
        return {
            "read": int(read),
            "unread": int(unread),
            "important": int(important),
            "archived": int(archived),
            "pending_review": int(pending_review),
        }

    def _set_sync_state(
        self, conn: sqlite3.Connection, name: str, value: str
    ) -> None:
        conn.execute(
            """
            INSERT INTO sync_state (name, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (name, value),
        )

    def _sync_search_index(self, conn: sqlite3.Connection, tweet_id: str) -> None:
        row = conn.execute(
            """
            SELECT tweet_id, text, author, url, category, tags_json, notes
            FROM bookmarks
            WHERE tweet_id = ?
            """,
            (tweet_id,),
        ).fetchone()
        conn.execute("DELETE FROM bookmarks_fts WHERE tweet_id = ?", (tweet_id,))
        if row is None:
            return
        conn.execute(
            """
            INSERT INTO bookmarks_fts (
                tweet_id, text, author, url, category, tags, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["tweet_id"],
                row["text"] or "",
                row["author"] or "",
                row["url"] or "",
                row["category"] or "",
                _tags_text(row["tags_json"]),
                row["notes"] or "",
            ),
        )

    def _rebuild_search_index(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM bookmarks_fts")
        rows = conn.execute("SELECT tweet_id FROM bookmarks").fetchall()
        for row in rows:
            self._sync_search_index(conn, str(row["tweet_id"]))

    def stats(self) -> dict[str, int]:
        self.init()
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
            classified = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE category IS NOT NULL"
            ).fetchone()[0]
            exported = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE export_path IS NOT NULL"
            ).fetchone()[0]
        return {
            "total": total,
            "classified": classified,
            "exported": exported,
        }

    def review_summary(self) -> dict[str, object]:
        self.init()
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE review_state = 'pending'"
            ).fetchone()[0]
            accepted = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE review_state = 'accepted'"
            ).fetchone()[0]
            reason_rows = conn.execute(
                """
                SELECT COALESCE(review_reason, 'unspecified') AS reason,
                       COUNT(*) AS count
                FROM bookmarks
                WHERE review_state = 'pending'
                GROUP BY COALESCE(review_reason, 'unspecified')
                ORDER BY count DESC, reason
                """
            ).fetchall()
        return {
            "total": int(total),
            "pending": int(pending),
            "accepted": int(accepted),
            "by_reason": {
                str(row["reason"]): int(row["count"]) for row in reason_rows
            },
        }

    def category_counts(self) -> list[tuple[str, int]]:
        self.init()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(category, 'Unclassified') AS category, COUNT(*) AS count
                FROM bookmarks
                GROUP BY COALESCE(category, 'Unclassified')
                ORDER BY count DESC, category
                """
            ).fetchall()
        return [(str(row["category"]), int(row["count"])) for row in rows]


def _bookmark_hash(
    tweet_id: str,
    url: str,
    text: str,
    author: str | None,
    created_at: str | None,
    raw_json: str,
) -> str:
    payload = {
        "tweet_id": tweet_id,
        "url": url,
        "text": text,
        "author": author,
        "created_at": created_at,
        "raw_json": _canonical_raw_json(raw_json),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical_raw_json(raw_json: str) -> object:
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json


def _tags_text(tags_json: str | None) -> str:
    if not tags_json:
        return ""
    try:
        parsed = json.loads(tags_json)
    except json.JSONDecodeError:
        return str(tags_json)
    if isinstance(parsed, list):
        return " ".join(str(tag) for tag in parsed)
    return str(parsed)


def _fts_query(query: str) -> str:
    terms = [term.strip() for term in query.split() if term.strip()]
    return " ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)
