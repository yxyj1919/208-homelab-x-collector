from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Bookmark, ClassificationResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    tweet_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    text TEXT NOT NULL,
    author TEXT,
    created_at TEXT,
    raw_json TEXT NOT NULL,
    category TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    reason TEXT,
    export_path TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_state (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            conn.executescript(SCHEMA)

    def upsert_bookmarks(self, bookmarks: Iterable[Bookmark]) -> int:
        self.init()
        count = 0
        with self.connect() as conn:
            for bookmark in bookmarks:
                conn.execute(
                    """
                    INSERT INTO bookmarks (
                        tweet_id, url, text, author, created_at, raw_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(tweet_id) DO UPDATE SET
                        url = excluded.url,
                        text = excluded.text,
                        author = excluded.author,
                        created_at = excluded.created_at,
                        raw_json = excluded.raw_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        bookmark.tweet_id,
                        bookmark.url,
                        bookmark.text,
                        bookmark.author,
                        bookmark.created_at,
                        json.dumps(bookmark.raw or {}, ensure_ascii=False),
                    ),
                )
                count += 1
        return count

    def iter_bookmarks(
        self,
        only_unclassified: bool = False,
        category: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        self.init()
        clauses = []
        params = []
        if only_unclassified:
            clauses.append("category IS NULL")
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = "LIMIT ?" if limit is not None else ""
        if limit is not None:
            params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT tweet_id, url, text, author, created_at, category, tags_json,
                       confidence, reason, export_path
                FROM bookmarks
                {where}
                ORDER BY COALESCE(created_at, imported_at) DESC, tweet_id DESC
                {limit_sql}
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_classification(
        self, tweet_id: str, result: ClassificationResult
    ) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE bookmarks
                SET category = ?, tags_json = ?, confidence = ?, reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tweet_id = ?
                """,
                (
                    result.category,
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
