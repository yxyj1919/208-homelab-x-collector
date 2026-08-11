from __future__ import annotations

import json
import sqlite3
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import Bookmark, ClassificationResult


CURRENT_SCHEMA_VERSION = 4


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
    provider TEXT,
    model TEXT,
    imported_count INTEGER NOT NULL DEFAULT 0,
    classified_count INTEGER NOT NULL DEFAULT 0,
    exported_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
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
                            content_hash, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                SELECT tweet_id, url, text, author, created_at, content_hash,
                       change_count, first_seen_at, last_seen_at, category,
                       category_source, tags_json, confidence, reason, export_path
                FROM bookmarks
                {where}
                ORDER BY COALESCE(created_at, imported_at) DESC, tweet_id DESC
                {limit_sql}
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_classification(
        self, tweet_id: str, result: ClassificationResult, source: str = "auto"
    ) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE bookmarks
                SET category = ?, category_source = ?, tags_json = ?,
                    confidence = ?, reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE tweet_id = ?
                """,
                (
                    result.category,
                    source,
                    json.dumps(result.tags, ensure_ascii=False),
                    result.confidence,
                    result.reason,
                    tweet_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Bookmark not found: {tweet_id}")

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
        provider: str | None = None,
        model: str | None = None,
    ) -> int:
        self.init()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO run_logs (
                    command, status, input_path, archive_dir, provider, model
                )
                VALUES (?, 'running', ?, ?, ?, ?)
                """,
                (
                    command,
                    str(input_path) if input_path else None,
                    str(archive_dir) if archive_dir else None,
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
        message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE run_logs
                SET status = ?, ended_at = CURRENT_TIMESTAMP,
                    imported_count = ?, classified_count = ?, exported_count = ?,
                    message = ?
                WHERE id = ?
                """,
                (
                    status,
                    imported_count,
                    classified_count,
                    exported_count,
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
                       archive_dir, provider, model, imported_count,
                       classified_count, exported_count, message
                FROM run_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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
