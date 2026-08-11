from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from xbookmarks.cli import main
from xbookmarks.storage import BookmarkStore


class PipelineTest(unittest.TestCase):
    def test_run_imports_classifies_and_exports(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sample = root / "samples" / "bookmarks.json"

        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            archive = base / "archive"

            exit_code = main(
                [
                    "--db",
                    str(db),
                    "run",
                    "--input",
                    str(sample),
                    "--archive-dir",
                    str(archive),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((archive / "_index" / "index.html").exists())
            self.assertTrue((archive / "VCF" / "2026-08-09_1001.html").exists())
            self.assertTrue((archive / "Networking" / "2026-08-09_1002.html").exists())
            self.assertTrue((archive / "AI" / "2026-08-09_1003.html").exists())

            with sqlite3.connect(db) as conn:
                total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
                self.assertEqual(total, 3)
                run = conn.execute(
                    """
                    SELECT status, imported_count, classified_count, exported_count,
                           provider, model
                    FROM run_logs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(run, ("succeeded", 3, 3, 3, "rules", "rules"))

    def test_export_removes_old_file_when_category_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            archive = base / "archive"
            sample = base / "bookmark.json"

            sample.write_text(
                json.dumps(
                    [
                        {
                            "id": "9001",
                            "text": "Python programming",
                            "created_at": "2026-08-10T10:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            main(
                [
                    "--db",
                    str(db),
                    "run",
                    "--input",
                    str(sample),
                    "--archive-dir",
                    str(archive),
                ]
            )
            self.assertTrue(
                (archive / "Programming" / "2026-08-10_9001.html").exists()
            )

            sample.write_text(
                json.dumps(
                    [
                        {
                            "id": "9001",
                            "text": "Docker Compose container",
                            "created_at": "2026-08-10T10:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            main(
                [
                    "--db",
                    str(db),
                    "run",
                    "--input",
                    str(sample),
                    "--archive-dir",
                    str(archive),
                    "--reclassify",
                ]
            )

            self.assertFalse((archive / "Programming" / "2026-08-10_9001.html").exists())
            self.assertTrue((archive / "DevOps" / "2026-08-10_9001.html").exists())

    def test_manual_category_is_not_overwritten_by_reclassify_by_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            archive = base / "archive"
            sample = base / "bookmark.json"

            sample.write_text(
                json.dumps(
                    [
                        {
                            "id": "9001",
                            "text": "Python programming",
                            "created_at": "2026-08-10T10:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            main(
                [
                    "--db",
                    str(db),
                    "run",
                    "--input",
                    str(sample),
                    "--archive-dir",
                    str(archive),
                ]
            )
            self.assertTrue(
                (archive / "Programming" / "2026-08-10_9001.html").exists()
            )

            exit_code = main(
                [
                    "--db",
                    str(db),
                    "set-category",
                    "9001",
                    "Learning",
                    "--tags",
                    "python,manual",
                    "--archive-dir",
                    str(archive),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertFalse(
                (archive / "Programming" / "2026-08-10_9001.html").exists()
            )
            self.assertTrue((archive / "Learning" / "2026-08-10_9001.html").exists())

            main(
                [
                    "--db",
                    str(db),
                    "classify",
                    "--all",
                ]
            )
            main(["--db", str(db), "export-html", "--archive-dir", str(archive)])

            self.assertTrue((archive / "Learning" / "2026-08-10_9001.html").exists())
            self.assertFalse(
                (archive / "Programming" / "2026-08-10_9001.html").exists()
            )

            with sqlite3.connect(db) as conn:
                category, source = conn.execute(
                    "SELECT category, category_source FROM bookmarks WHERE tweet_id = ?",
                    ("9001",),
                ).fetchone()
            self.assertEqual(category, "Learning")
            self.assertEqual(source, "manual")

    def test_include_manual_allows_reclassify_to_overwrite_manual_category(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmark.json"

            sample.write_text(
                json.dumps(
                    [
                        {
                            "id": "9001",
                            "text": "Python programming",
                            "created_at": "2026-08-10T10:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            main(["--db", str(db), "run", "--input", str(sample)])
            main(["--db", str(db), "set-category", "9001", "Learning"])
            main(["--db", str(db), "classify", "--all", "--include-manual"])

            with sqlite3.connect(db) as conn:
                category, source = conn.execute(
                    "SELECT category, category_source FROM bookmarks WHERE tweet_id = ?",
                    ("9001",),
                ).fetchone()
            self.assertEqual(category, "Programming")
            self.assertEqual(source, "auto")

    def test_import_deduplicates_input_and_tracks_unchanged_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmarks.json"

            sample.write_text(
                json.dumps(
                    [
                        {"id": "9001", "text": "Python programming"},
                        {"id": "9001", "text": "Python programming"},
                    ]
                ),
                encoding="utf-8",
            )

            first_output = StringIO()
            with redirect_stdout(first_output):
                main(["--db", str(db), "import", str(sample)])
            second_output = StringIO()
            with redirect_stdout(second_output):
                main(["--db", str(db), "import", str(sample)])

            with sqlite3.connect(db) as conn:
                total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
                change_count = conn.execute(
                    "SELECT change_count FROM bookmarks WHERE tweet_id = '9001'"
                ).fetchone()[0]

        self.assertEqual(total, 1)
        self.assertEqual(change_count, 0)
        self.assertIn("inserted=1", first_output.getvalue())
        self.assertIn("duplicates=1", first_output.getvalue())
        self.assertIn("unchanged=1", second_output.getvalue())

    def test_changed_auto_classified_bookmark_is_marked_for_reclassification(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            archive = base / "archive"
            sample = base / "bookmark.json"

            sample.write_text(
                json.dumps(
                    [{"id": "9001", "text": "Python programming"}]
                ),
                encoding="utf-8",
            )
            main(["--db", str(db), "run", "--input", str(sample), "--archive-dir", str(archive)])

            sample.write_text(
                json.dumps(
                    [{"id": "9001", "text": "Docker Compose container"}]
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                main(["--db", str(db), "import", str(sample)])

            with sqlite3.connect(db) as conn:
                category, source, change_count = conn.execute(
                    """
                    SELECT category, category_source, change_count
                    FROM bookmarks
                    WHERE tweet_id = '9001'
                    """
                ).fetchone()

        self.assertIn("updated=1", output.getvalue())
        self.assertIsNone(category)
        self.assertEqual(source, "auto")
        self.assertEqual(change_count, 1)

    def test_changed_manual_bookmark_preserves_manual_category(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmark.json"

            sample.write_text(
                json.dumps(
                    [{"id": "9001", "text": "Python programming"}]
                ),
                encoding="utf-8",
            )
            main(["--db", str(db), "run", "--input", str(sample)])
            main(["--db", str(db), "set-category", "9001", "Learning"])

            sample.write_text(
                json.dumps(
                    [{"id": "9001", "text": "Docker Compose container"}]
                ),
                encoding="utf-8",
            )
            main(["--db", str(db), "import", str(sample)])

            with sqlite3.connect(db) as conn:
                category, source, change_count = conn.execute(
                    """
                    SELECT category, category_source, change_count
                    FROM bookmarks
                    WHERE tweet_id = '9001'
                    """
                ).fetchone()

        self.assertEqual(category, "Learning")
        self.assertEqual(source, "manual")
        self.assertEqual(change_count, 1)


class StorageMigrationTest(unittest.TestCase):
    def test_init_migrates_v1_database_to_v2(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "bookmarks.sqlite"
            with sqlite3.connect(db) as conn:
                conn.executescript(
                    """
                    CREATE TABLE bookmarks (
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
                    CREATE TABLE sync_state (
                        name TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO bookmarks (tweet_id, url, text, raw_json)
                    VALUES ('1001', 'https://x.com/example/status/1001', 'VMware note', '{}');
                    """
                )

            store = BookmarkStore(db)
            store.init()

            with sqlite3.connect(db) as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(bookmarks)")
                }
                versions = [
                    row[0]
                    for row in conn.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                indexes = {
                    row[1] for row in conn.execute("PRAGMA index_list(bookmarks)")
                }
                category_source = conn.execute(
                    "SELECT category_source FROM bookmarks WHERE tweet_id = '1001'"
                ).fetchone()[0]

        self.assertIn("category_source", columns)
        self.assertEqual(versions, [1, 2, 3, 4])
        self.assertIn("idx_bookmarks_category", indexes)
        self.assertIn("idx_bookmarks_created_at", indexes)
        self.assertEqual(category_source, "auto")

    def test_new_database_initializes_at_current_schema_version(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")

            version = store.schema_version()

        self.assertEqual(version, 4)

    def test_run_logs_failed_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            missing_input = base / "missing.json"

            with self.assertRaises(FileNotFoundError):
                main(["--db", str(db), "run", "--input", str(missing_input)])

            store = BookmarkStore(db)
            logs = store.list_run_logs()
            state = store.sync_state()

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "failed")
        self.assertIn("missing.json", logs[0]["message"])
        self.assertEqual(state["last_run_status"], "failed")

    def test_sync_status_and_run_log_commands_report_latest_run(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sample = root / "samples" / "bookmarks.json"

        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            archive = base / "archive"
            main(
                [
                    "--db",
                    str(db),
                    "run",
                    "--input",
                    str(sample),
                    "--archive-dir",
                    str(archive),
                ]
            )

            status_output = StringIO()
            with redirect_stdout(status_output):
                main(["--db", str(db), "sync-status"])

            log_output = StringIO()
            with redirect_stdout(log_output):
                main(["--db", str(db), "run-log", "--limit", "1"])

        self.assertIn("last_run_status=succeeded", status_output.getvalue())
        self.assertIn("latest_run=id=1 status=succeeded command=run", status_output.getvalue())
        self.assertIn("1\tsucceeded\trun", log_output.getvalue())
        self.assertIn("imported=3", log_output.getvalue())


if __name__ == "__main__":
    unittest.main()
