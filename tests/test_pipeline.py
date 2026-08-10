from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xbookmarks.cli import main


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


if __name__ == "__main__":
    unittest.main()
