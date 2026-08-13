from __future__ import annotations

import json
import os
import sqlite3
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

from xbookmarks.cli import main
from xbookmarks.connectors import (
    ConnectorAuthError,
    ConnectorCapabilityError,
    ConnectorOptions,
    ConnectorRateLimitError,
    JsonFileConnector,
    XApiConnector,
    XArchiveJsonConnector,
    build_connector,
    read_bearer_token,
)
from xbookmarks.exporter import export_html, export_markdown
from xbookmarks.models import Bookmark, ClassificationResult
from xbookmarks.secrets import SecretStore
from xbookmarks.services import BookmarkService
from xbookmarks.storage import CURRENT_SCHEMA_VERSION, BookmarkStore
from xbookmarks.web import INDEX_HTML, XBookmarksHandler


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
                    SELECT status, imported_count, inserted_count, updated_count,
                           unchanged_count, duplicate_count, classified_count,
                           exported_count, connector, pages_fetched,
                           source_count, has_more, provider, model
                    FROM run_logs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(
                    run,
                    (
                        "succeeded",
                        3,
                        3,
                        0,
                        0,
                        0,
                        3,
                        3,
                        "json-file",
                        0,
                        3,
                        0,
                        "rules",
                        "rules",
                    ),
                )

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

    def test_export_markdown_writes_obsidian_front_matter(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            store = BookmarkStore(base / "bookmarks.sqlite")
            archive = base / "obsidian"
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="VMware Cloud Foundation note",
                        author="vmw_notes",
                        created_at="2026-08-10T10:00:00Z",
                        raw={
                            "source": "chrome-extension",
                            "media": [
                                {
                                    "type": "photo",
                                    "url": "https://example.com/image.jpg",
                                    "alt_text": "diagram",
                                }
                            ],
                        },
                    )
                ]
            )
            store.save_classification(
                "9001",
                ClassificationResult(
                    category="VCF",
                    tags=["vmware", "homelab"],
                    confidence=0.95,
                    reason="Matched VCF keywords.",
                ),
                provider="rules",
            )
            store.update_bookmark("9001", read_state="read", notes="Review later")

            count = export_markdown(store, archive)
            markdown_path = archive / "VCF" / "2026-08-10_9001.md"
            markdown_file_exists = markdown_path.exists()
            content = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(count, 1)
        self.assertTrue(markdown_file_exists)
        self.assertIn('tweet_id: "9001"', content)
        self.assertIn('url: "https://x.com/example/status/9001"', content)
        self.assertIn('author: "vmw_notes"', content)
        self.assertIn('created_at: "2026-08-10T10:00:00Z"', content)
        self.assertIn('category: "VCF"', content)
        self.assertIn('  - "vmware"', content)
        self.assertIn('  - "homelab"', content)
        self.assertIn('source: "chrome-extension"', content)
        self.assertIn('provider: "rules"', content)
        self.assertIn("confidence: 0.95", content)
        self.assertIn('read_state: "read"', content)
        self.assertIn("VMware Cloud Foundation note", content)
        self.assertIn("![diagram](https://example.com/image.jpg)", content)
        self.assertIn("Review later", content)

    def test_export_markdown_command_uses_archive_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmark.json"
            archive = base / "obsidian"
            sample.write_text(
                json.dumps(
                    [
                        {
                            "id": "9001",
                            "text": "Kubernetes cluster note",
                            "created_at": "2026-08-10T10:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            main(["--db", str(db), "run", "--input", str(sample)])

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--db",
                        str(db),
                        "export-markdown",
                        "--archive-dir",
                        str(archive),
                    ]
                )

            markdown_path = archive / "Kubernetes" / "2026-08-10_9001.md"
            index_path = archive / "_index" / "index.md"
            markdown_file_exists = markdown_path.exists()
            index_file_exists = index_path.exists()

        self.assertEqual(exit_code, 0)
        self.assertIn("Exported 1 bookmark Markdown file(s)", output.getvalue())
        self.assertTrue(markdown_file_exists)
        self.assertTrue(index_file_exists)

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

    def test_xarchive_json_import_maps_folders_to_category_and_tags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            archive = base / "archive"
            sample = base / "xarchive.json"
            sample.write_text(
                json.dumps(
                    {
                        "export_metadata": {"tool": "xarchive"},
                        "folders": [{"id": "f1", "name": "VCF"}],
                        "bookmarks": [
                            {
                                "tweet_id": "9001",
                                "status": "available",
                                "created_at": "2026-08-10T10:00:00Z",
                                "full_text": "VMware Cloud Foundation note",
                                "folders": ["VCF", "Homelab"],
                                "author": {
                                    "screen_name": "vmw_notes",
                                    "name": "VMware Notes",
                                },
                                "metrics": {"likes": 3},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--db",
                    str(db),
                    "run",
                    "--connector",
                    "xarchive-json",
                    "--input",
                    str(sample),
                    "--archive-dir",
                    str(archive),
                ]
            )

            with sqlite3.connect(db) as conn:
                category, source, tags_json, raw_json = conn.execute(
                    """
                    SELECT category, category_source, tags_json, raw_json
                    FROM bookmarks
                    WHERE tweet_id = '9001'
                    """
                ).fetchone()
            exported_file_exists = (
                archive / "VCF" / "2026-08-10_9001.html"
            ).exists()

        self.assertEqual(exit_code, 0)
        self.assertEqual(category, "VCF")
        self.assertEqual(source, "auto")
        self.assertEqual(json.loads(tags_json), ["VCF", "Homelab"])
        self.assertEqual(json.loads(raw_json)["metrics"]["likes"], 3)
        self.assertTrue(exported_file_exists)

    def test_xarchive_json_import_preserves_manual_category(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "xarchive.json"
            sample.write_text(
                json.dumps(
                    {
                        "bookmarks": [
                            {
                                "tweet_id": "9001",
                                "status": "available",
                                "full_text": "VMware Cloud Foundation note",
                                "folders": ["VCF"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            main(
                [
                    "--db",
                    str(db),
                    "import",
                    str(sample),
                    "--connector",
                    "xarchive-json",
                ]
            )
            main(["--db", str(db), "set-category", "9001", "Manual"])
            main(
                [
                    "--db",
                    str(db),
                    "import",
                    str(sample),
                    "--connector",
                    "xarchive-json",
                ]
            )

            with sqlite3.connect(db) as conn:
                category, source = conn.execute(
                    """
                    SELECT category, category_source
                    FROM bookmarks
                    WHERE tweet_id = '9001'
                    """
                ).fetchone()

        self.assertEqual(category, "Manual")
        self.assertEqual(source, "manual")

    def test_update_command_sets_notes_tags_and_status_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmark.json"
            sample.write_text(
                json.dumps([{"id": "9001", "text": "VMware lifecycle"}]),
                encoding="utf-8",
            )
            main(["--db", str(db), "import", str(sample)])

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--db",
                        str(db),
                        "update",
                        "9001",
                        "--tags",
                        "vmware, homelab",
                        "--notes",
                        "read later",
                        "--read-state",
                        "read",
                        "--important",
                        "--archived",
                    ]
                )

            with sqlite3.connect(db) as conn:
                tags_json, notes, read_state, important, archived = conn.execute(
                    """
                    SELECT tags_json, notes, read_state, important, archived
                    FROM bookmarks
                    WHERE tweet_id = '9001'
                    """
                ).fetchone()

        self.assertEqual(exit_code, 0)
        self.assertIn("Updated bookmark 9001", output.getvalue())
        self.assertEqual(json.loads(tags_json), ["vmware", "homelab"])
        self.assertEqual(notes, "read later")
        self.assertEqual(read_state, "read")
        self.assertEqual(important, 1)
        self.assertEqual(archived, 1)

    def test_update_command_can_clear_boolean_flags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmark.json"
            sample.write_text(
                json.dumps([{"id": "9001", "text": "VMware lifecycle"}]),
                encoding="utf-8",
            )
            main(["--db", str(db), "import", str(sample)])
            main(["--db", str(db), "update", "9001", "--important", "--archived"])
            main(
                [
                    "--db",
                    str(db),
                    "update",
                    "9001",
                    "--no-important",
                    "--no-archived",
                ]
            )

            with sqlite3.connect(db) as conn:
                important, archived = conn.execute(
                    """
                    SELECT important, archived
                    FROM bookmarks
                    WHERE tweet_id = '9001'
                    """
                ).fetchone()

        self.assertEqual(important, 0)
        self.assertEqual(archived, 0)

    def test_update_command_requires_at_least_one_field(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["--db", ":memory:", "update", "9001"])

        self.assertEqual(ctx.exception.code, 2)

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

    def test_import_records_json_file_connector_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmarks.json"

            sample.write_text(
                json.dumps([{"id": "9001", "text": "Python programming"}]),
                encoding="utf-8",
            )

            exit_code = main(["--db", str(db), "import", str(sample)])

            state = BookmarkStore(db).sync_state()

        self.assertEqual(exit_code, 0)
        self.assertEqual(state["last_connector"], "json-file")
        self.assertEqual(state["json-file.source_path"], str(sample))
        self.assertRegex(state["json-file.cursor"], r"^\d+:\d+$")

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

    def test_search_command_uses_full_text_index(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            sample = base / "bookmark.json"
            sample.write_text(
                json.dumps(
                    [
                        {
                            "id": "9001",
                            "text": "VMware Cloud Foundation lifecycle note",
                            "author": "vcf-admin",
                            "created_at": "2026-08-10T10:00:00Z",
                        },
                        {
                            "id": "9002",
                            "text": "Python programming",
                            "author": "dev",
                            "created_at": "2026-08-10T11:00:00Z",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            main(["--db", str(db), "run", "--input", str(sample)])

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--db", str(db), "search", "lifecycle"])

        self.assertEqual(exit_code, 0)
        self.assertIn("9001", output.getvalue())
        self.assertNotIn("9002", output.getvalue())

    def test_search_indexes_category_tags_and_notes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="Release detail",
                    )
                ]
            )
            store.save_classification(
                "9001",
                ClassificationResult(
                    category="Kubernetes",
                    tags=["vks", "homelab"],
                    confidence=0.9,
                    reason="test",
                ),
            )
            store.save_notes("9001", "Obsidian follow up")

            by_category = store.search_bookmarks("Kubernetes")
            by_tag = store.search_bookmarks("homelab")
            by_note = store.search_bookmarks("Obsidian")

        self.assertEqual([row["tweet_id"] for row in by_category], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_tag], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_note], ["9001"])

    def test_bookmark_metadata_updates_support_ui_filters(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="VKS release note",
                    )
                ]
            )
            store.update_bookmark(
                "9001",
                category="Kubernetes",
                tags=["vks", "manual"],
                notes="follow up",
                read_state="read",
                important=True,
                archived=True,
            )

            by_category = store.list_bookmarks(category="Kubernetes")
            by_read = store.list_bookmarks(status="read")
            by_important = store.list_bookmarks(status="important")
            by_archived = store.list_bookmarks(status="archived")
            by_pending = store.list_bookmarks(status="pending_review")
            by_query = store.list_bookmarks(query="follow")

        self.assertEqual([row["tweet_id"] for row in by_category], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_read], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_important], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_archived], ["9001"])
        self.assertEqual(by_pending, [])
        self.assertEqual([row["tweet_id"] for row in by_query], ["9001"])

    def test_new_and_low_confidence_bookmarks_enter_review_queue(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="Unmatched note",
                    )
                ]
            )

            new_pending = store.list_bookmarks(status="pending_review")
            store.save_classification(
                "9001",
                ClassificationResult(
                    category="General",
                    tags=[],
                    confidence=0.2,
                    reason="No category keyword matched.",
                ),
                provider="rules",
            )
            low_confidence = store.get_bookmark("9001")
            store.update_bookmark("9001", review_state="accepted")
            accepted = store.get_bookmark("9001")
            after_accept = store.list_bookmarks(status="pending_review")

        self.assertEqual([row["tweet_id"] for row in new_pending], ["9001"])
        self.assertEqual(low_confidence["review_state"], "pending")
        self.assertEqual(low_confidence["review_reason"], "low-confidence")
        self.assertEqual(accepted["review_state"], "accepted")
        self.assertIsNone(accepted["review_reason"])
        self.assertIsNotNone(accepted["reviewed_at"])
        self.assertEqual(after_accept, [])

    def test_review_summary_counts_pending_reasons(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="New note",
                    ),
                    Bookmark(
                        tweet_id="9002",
                        url="https://x.com/example/status/9002",
                        text="General note",
                    ),
                    Bookmark(
                        tweet_id="9003",
                        url="https://x.com/example/status/9003",
                        text="Accepted note",
                    ),
                ]
            )
            store.save_classification(
                "9002",
                ClassificationResult(
                    category="General",
                    tags=[],
                    confidence=0.2,
                    reason="No category keyword matched.",
                ),
                provider="rules",
            )
            store.update_bookmark("9003", review_state="accepted")

            summary = BookmarkService(store).review_summary()

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["pending"], 2)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["by_reason"], {"low-confidence": 1, "new-import": 1})

    def test_review_summary_command_prints_reason_counts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "bookmarks.sqlite"
            store = BookmarkStore(db)
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="New note",
                    )
                ]
            )

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--db", str(db), "review-summary"])

        self.assertEqual(exit_code, 0)
        self.assertIn("total=1 pending=1 accepted=0", output.getvalue())
        self.assertIn("new-import\t1", output.getvalue())

    def test_bookmark_service_bulk_updates_selected_bookmarks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            service = BookmarkService(store)
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="First note",
                    ),
                    Bookmark(
                        tweet_id="9002",
                        url="https://x.com/example/status/9002",
                        text="Second note",
                    ),
                    Bookmark(
                        tweet_id="9003",
                        url="https://x.com/example/status/9003",
                        text="Third note",
                    ),
                ]
            )

            accept_result = service.bulk_update_bookmarks(
                {"action": "accept", "tweet_ids": ["9001", "missing"]}
            )
            archive_result = service.bulk_update_bookmarks(
                {"action": "archive", "tweet_ids": ["9001", "9002"]}
            )
            category_result = service.bulk_update_bookmarks(
                {
                    "action": "category",
                    "tweet_ids": ["9002", "9003"],
                    "category": "VCF",
                }
            )
            row1 = store.get_bookmark("9001")
            row2 = store.get_bookmark("9002")
            row3 = store.get_bookmark("9003")

        self.assertEqual(accept_result, {"updated": 1, "missing": ["missing"]})
        self.assertEqual(archive_result, {"updated": 2, "missing": []})
        self.assertEqual(category_result, {"updated": 2, "missing": []})
        self.assertEqual(row1["review_state"], "accepted")
        self.assertEqual(row1["archived"], 1)
        self.assertEqual(row2["category"], "VCF")
        self.assertEqual(row2["category_source"], "manual")
        self.assertEqual(row2["archived"], 1)
        self.assertEqual(row3["category"], "VCF")

    def test_bookmark_service_rejects_invalid_bulk_updates(self) -> None:
        service = BookmarkService(BookmarkStore(Path(":memory:")))

        with self.assertRaisesRegex(ValueError, "tweet_ids"):
            service.bulk_update_bookmarks({"action": "accept", "tweet_ids": []})
        with self.assertRaisesRegex(ValueError, "action"):
            service.bulk_update_bookmarks({"action": "delete", "tweet_ids": ["9001"]})
        with self.assertRaisesRegex(ValueError, "category"):
            service.bulk_update_bookmarks({"action": "category", "tweet_ids": ["9001"]})

    def test_bookmark_service_normalizes_payloads_and_filters(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            service = BookmarkService(store)
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="VMware lifecycle",
                        author="vcf-admin",
                    )
                ]
            )

            patch_body = service.update_bookmark(
                "9001",
                {
                    "category": "VCF",
                    "tags": [" vmware ", "", "homelab"],
                    "notes": "read later",
                    "read_state": "read",
                    "important": True,
                    "archived": False,
                },
            )
            accepted_body = service.update_bookmark(
                "9001", {"review_state": "accepted"}
            )
            pending_body = service.update_bookmark("9001", {"review_state": "pending"})
            list_body = service.list_bookmarks(category="VCF", status="important")

        self.assertEqual(patch_body["item"]["category"], "VCF")
        self.assertEqual(patch_body["item"]["tags"], ["vmware", "homelab"])
        self.assertEqual(accepted_body["item"]["review_state"], "accepted")
        self.assertEqual(pending_body["item"]["review_state"], "pending")
        self.assertEqual(pending_body["item"]["review_reason"], "manual-pending")
        self.assertEqual(list_body["items"][0]["tweet_id"], "9001")

    def test_bookmark_service_rejects_invalid_api_inputs(self) -> None:
        service = BookmarkService(BookmarkStore(Path(":memory:")))

        with self.assertRaisesRegex(ValueError, "Unsupported field"):
            service.update_bookmark("9001", {"unknown": True})
        with self.assertRaisesRegex(ValueError, "limit must be between"):
            service.list_bookmarks(limit=0)
        with self.assertRaisesRegex(ValueError, "review_state"):
            service.update_bookmark("9001", {"review_state": "done"})

    def test_bookmark_service_imports_extension_payload(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            service = BookmarkService(store)

            result = service.import_extension_bookmarks(
                {
                    "source": "chrome-extension",
                    "source_url": "https://x.com/i/bookmarks",
                    "export_html": False,
                    "items": [
                        {
                            "tweet_id": "9101",
                            "url": "https://x.com/example/status/9101",
                            "text": "VMware Cloud Foundation bookmark",
                            "author": "example",
                            "created_at": "2026-08-11T10:00:00Z",
                        },
                        {
                            "tweet_id": "9101",
                            "url": "https://x.com/example/status/9101",
                            "text": "VMware Cloud Foundation bookmark",
                            "author": "example",
                        },
                    ],
                }
            )
            items = service.list_bookmarks(query="Foundation")["items"]
            state = store.sync_state()

        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(items[0]["tweet_id"], "9101")
        self.assertEqual(items[0]["author"], "example")
        self.assertEqual(state["last_connector"], "chrome-extension")
        self.assertEqual(state["chrome-extension.source_url"], "https://x.com/i/bookmarks")

    def test_bookmark_service_imports_graphql_author_object(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            service = BookmarkService(store)

            service.import_extension_bookmarks(
                {
                    "source": "chrome-extension-graphql",
                    "source_url": "https://x.com/i/bookmarks",
                    "export_html": False,
                    "items": [
                        {
                            "tweet_id": "9201",
                            "url": "https://x.com/i/status/9201",
                            "full_text": "Kubernetes bookmark",
                            "author": {
                                "user_id": "42",
                                "screen_name": "k8s_notes",
                                "name": "K8s Notes",
                            },
                        },
                    ],
                }
            )
            item = service.list_bookmarks(query="Kubernetes")["items"][0]

        self.assertEqual(item["author"], "k8s_notes")
        self.assertEqual(item["author_profile"]["screen_name"], "k8s_notes")

    def test_bookmark_service_allows_final_graphql_export_summary(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            service = BookmarkService(store)
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9301",
                        url="https://x.com/i/status/9301",
                        text="VMware bookmark",
                    )
                ]
            )

            result = service.import_extension_bookmarks(
                {
                    "source": "chrome-extension-graphql",
                    "source_url": "https://x.com/i/bookmarks",
                    "export_html": True,
                    "summary": {
                        "source": 101,
                        "unique": 101,
                        "imported": 101,
                        "inserted": 1,
                        "updated": 99,
                        "unchanged": 1,
                        "duplicates": 0,
                        "classified": 77,
                    },
                    "items": [],
                }
            )
            status = service.sync_status()["summary"]

        self.assertEqual(result["total_seen"], 101)
        self.assertEqual(result["classified"], 78)
        self.assertEqual(status["source_count"], 101)
        self.assertEqual(status["updated"], 99)
        self.assertEqual(status["classified"], 78)
        self.assertEqual(status["exported"], 1)

    def test_bookmark_service_exposes_xarchive_rich_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            service = BookmarkService(store)
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/i/status/9001",
                        text="VMware note",
                        author="42",
                        raw={
                            "author": {
                                "user_id": "42",
                                "screen_name": "vmw_notes",
                                "name": "VMware Notes",
                                "verified": True,
                            },
                            "media": [
                                {
                                    "type": "photo",
                                    "url": "https://example.com/image.jpg",
                                    "alt_text": "diagram",
                                }
                            ],
                            "card": {
                                "url": "https://example.com/post",
                                "title": "Linked post",
                                "description": "Useful reference",
                            },
                            "quoted_tweet": {
                                "tweet_id": "9000",
                                "full_text": "Quoted note",
                                "author": {"screen_name": "quoted"},
                            },
                        },
                    )
                ]
            )

            item = service.list_bookmarks()["items"][0]

        self.assertEqual(item["author_profile"]["screen_name"], "vmw_notes")
        self.assertTrue(item["author_profile"]["verified"])
        self.assertEqual(item["media"][0]["url"], "https://example.com/image.jpg")
        self.assertEqual(item["card"]["title"], "Linked post")
        self.assertEqual(item["quoted_tweet"]["tweet_id"], "9000")

    def test_export_html_renders_xarchive_rich_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            store = BookmarkStore(base / "bookmarks.sqlite")
            archive = base / "archive"
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/i/status/9001",
                        text="VMware note",
                        author="42",
                        created_at="2026-08-10T10:00:00Z",
                        raw={
                            "author": {
                                "user_id": "42",
                                "screen_name": "vmw_notes",
                                "name": "VMware Notes",
                            },
                            "media": [
                                {
                                    "type": "photo",
                                    "url": "https://example.com/image.jpg",
                                    "alt_text": "diagram",
                                }
                            ],
                            "card": {
                                "url": "https://example.com/post",
                                "title": "Linked post",
                                "description": "Useful reference",
                            },
                            "quoted_tweet": {
                                "tweet_id": "9000",
                                "full_text": "Quoted note",
                                "author": {"screen_name": "quoted"},
                            },
                        },
                    )
                ]
            )

            export_html(store, archive)
            html_text = (archive / "General" / "2026-08-10_9001.html").read_text(
                encoding="utf-8"
            )

        self.assertIn("VMware Notes (@vmw_notes)", html_text)
        self.assertIn("https://example.com/image.jpg", html_text)
        self.assertIn("Linked post", html_text)
        self.assertIn("Quoted note", html_text)

    def test_web_api_lists_and_updates_bookmarks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9001",
                        url="https://x.com/example/status/9001",
                        text="VMware lifecycle",
                        author="vcf-admin",
                    )
                ]
            )

            class Handler(XBookmarksHandler):
                bookmark_service = BookmarkService(store)

            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            except PermissionError as exc:
                raise unittest.SkipTest("local port binding is not permitted") from exc
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                list_body = _read_json_url(f"{base_url}/api/bookmarks?query=VMware")
                patch_body = _read_json_url(
                    f"{base_url}/api/bookmarks/9001",
                    method="PATCH",
                    payload={
                        "category": "VCF",
                        "tags": ["vmware"],
                        "notes": "read later",
                        "read_state": "read",
                        "important": True,
                        "archived": False,
                    },
                )
                filtered_body = _read_json_url(
                    f"{base_url}/api/bookmarks?category=VCF&status=important"
                )
                accept_body = _read_json_url(
                    f"{base_url}/api/bookmarks/9001",
                    method="PATCH",
                    payload={"review_state": "accepted"},
                )
                pending_body = _read_json_url(
                    f"{base_url}/api/bookmarks?status=pending_review"
                )
                review_summary = _read_json_url(f"{base_url}/api/review-summary")
                bulk_body = _read_json_url(
                    f"{base_url}/api/bookmarks/bulk",
                    method="POST",
                    payload={"action": "archive", "tweet_ids": ["9001"]},
                )
                archived_body = _read_json_url(
                    f"{base_url}/api/bookmarks?status=archived"
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(list_body["items"][0]["tweet_id"], "9001")
        self.assertEqual(patch_body["item"]["category"], "VCF")
        self.assertEqual(patch_body["item"]["read_state"], "read")
        self.assertTrue(patch_body["item"]["important"])
        self.assertEqual(filtered_body["items"][0]["tweet_id"], "9001")
        self.assertEqual(accept_body["item"]["review_state"], "accepted")
        self.assertEqual(pending_body["items"], [])
        self.assertEqual(review_summary["pending"], 0)
        self.assertEqual(review_summary["accepted"], 1)
        self.assertEqual(bulk_body, {"updated": 1, "missing": []})
        self.assertEqual(archived_body["items"][0]["tweet_id"], "9001")

    def test_web_ui_exposes_review_queue_details(self) -> None:
        self.assertIn("Pending review", INDEX_HTML)
        self.assertIn("review-summary", INDEX_HTML)
        self.assertIn("loadReviewSummary", INDEX_HTML)
        self.assertIn("low-confidence", INDEX_HTML)
        self.assertIn("content-changed", INDEX_HTML)
        self.assertIn("review-detail", INDEX_HTML)
        self.assertIn("reviewReasonLabel", INDEX_HTML)
        self.assertIn("formatConfidence", INDEX_HTML)
        self.assertIn("Provider", INDEX_HTML)
        self.assertIn("acceptedId", INDEX_HTML)
        self.assertIn("skip-review", INDEX_HTML)
        self.assertIn("mark-pending", INDEX_HTML)
        self.assertIn("skippedReviewIds", INDEX_HTML)
        self.assertIn("skipSelected", INDEX_HTML)
        self.assertIn("markPendingSelected", INDEX_HTML)
        self.assertIn("bulk-actions", INDEX_HTML)
        self.assertIn("bulkUpdateSelected", INDEX_HTML)
        self.assertIn("/api/bookmarks/bulk", INDEX_HTML)
        self.assertIn("bulk-category", INDEX_HTML)
        self.assertIn("ai-classify", INDEX_HTML)
        self.assertIn("ai-dialog", INDEX_HTML)
        self.assertIn("ai-category", INDEX_HTML)
        self.assertIn("ai-run", INDEX_HTML)
        self.assertIn("openAiDialog", INDEX_HTML)
        self.assertIn("renderAiCategoryOptions", INDEX_HTML)
        self.assertIn("runAiClassify", INDEX_HTML)
        self.assertIn("startAiProgress", INDEX_HTML)
        self.assertIn("elapsed", INDEX_HTML)
        self.assertIn("/api/classify", INDEX_HTML)
        self.assertIn("mediaPreviewHtml", INDEX_HTML)
        self.assertIn("item-media", INDEX_HTML)

    def test_web_api_imports_extension_bookmarks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")

            class Handler(XBookmarksHandler):
                bookmark_service = BookmarkService(store)

            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            except PermissionError as exc:
                raise unittest.SkipTest("local port binding is not permitted") from exc
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                import_body = _read_json_url(
                    f"{base_url}/api/extension/bookmarks",
                    method="POST",
                    payload={
                        "source": "chrome-extension",
                        "source_url": "https://x.com/i/bookmarks",
                        "export_html": False,
                        "items": [
                            {
                                "tweet_id": "9101",
                                "url": "https://x.com/example/status/9101",
                                "text": "VMware Cloud Foundation bookmark",
                                "author": "example",
                                "created_at": "2026-08-11T10:00:00Z",
                            },
                            {
                                "tweet_id": "9101",
                                "url": "https://x.com/example/status/9101",
                                "text": "VMware Cloud Foundation bookmark",
                            },
                        ],
                    },
                )
                list_body = _read_json_url(f"{base_url}/api/bookmarks?query=Foundation")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            state = store.sync_state()

        self.assertEqual(import_body["inserted"], 1)
        self.assertEqual(import_body["duplicates"], 1)
        self.assertEqual(list_body["items"][0]["tweet_id"], "9101")
        self.assertEqual(state["last_connector"], "chrome-extension")
        self.assertEqual(state["chrome-extension.source_url"], "https://x.com/i/bookmarks")

    def test_web_api_classifies_bookmarks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9201",
                        url="https://x.com/example/status/9201",
                        text="Docker Compose deployment guide",
                        author="example",
                    )
                ]
            )
            store.save_classification(
                "9201",
                ClassificationResult(
                    category="General",
                    tags=[],
                    confidence=0.2,
                    reason="Seeded General category.",
                ),
                provider="rules",
            )

            class Handler(XBookmarksHandler):
                bookmark_service = BookmarkService(store)

            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            except PermissionError as exc:
                raise unittest.SkipTest("local port binding is not permitted") from exc
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                classify_body = _read_json_url(
                    f"{base_url}/api/classify",
                    method="POST",
                    payload={
                        "provider": "rules",
                        "category": "General",
                        "limit": 1,
                        "reclassify": True,
                        "export_html": False,
                    },
                )
                list_body = _read_json_url(f"{base_url}/api/bookmarks?category=DevOps")
                sync_body = _read_json_url(f"{base_url}/api/sync-status")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(classify_body["classified"], 1)
        self.assertEqual(classify_body["provider"], "rules")
        self.assertEqual(list_body["items"][0]["tweet_id"], "9201")
        self.assertEqual(list_body["items"][0]["category"], "DevOps")
        self.assertEqual(sync_body["summary"]["connector"], "")
        self.assertEqual(sync_body["summary"]["classified"], 1)

    def test_bookmark_service_classifies_bookmarks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")
            service = BookmarkService(store)
            store.upsert_bookmarks(
                [
                    Bookmark(
                        tweet_id="9301",
                        url="https://x.com/example/status/9301",
                        text="Docker Compose deployment guide",
                        author="example",
                    )
                ]
            )
            store.save_classification(
                "9301",
                ClassificationResult(
                    category="General",
                    tags=[],
                    confidence=0.2,
                    reason="Seeded General category.",
                ),
                provider="rules",
            )

            result = service.classify_bookmarks(
                {
                    "provider": "rules",
                    "category": "General",
                    "limit": 1,
                    "reclassify": True,
                    "export_html": False,
                }
            )
            rows = store.list_bookmarks(category="DevOps")

        self.assertEqual(result["classified"], 1)
        self.assertEqual(rows[0]["tweet_id"], "9301")
        self.assertEqual(rows[0]["classification_provider"], "rules")


class StorageMigrationTest(unittest.TestCase):
    def test_init_migrates_v1_database_to_current_schema(self) -> None:
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
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                category_source = conn.execute(
                    "SELECT category_source FROM bookmarks WHERE tweet_id = '1001'"
                ).fetchone()[0]
                review_state = conn.execute(
                    "SELECT review_state FROM bookmarks WHERE tweet_id = '1001'"
                ).fetchone()[0]
                search_count = conn.execute(
                    "SELECT COUNT(*) FROM bookmarks_fts WHERE bookmarks_fts MATCH 'VMware'"
                ).fetchone()[0]

        self.assertIn("category_source", columns)
        self.assertIn("notes", columns)
        self.assertIn("read_state", columns)
        self.assertIn("important", columns)
        self.assertIn("archived", columns)
        self.assertIn("classification_provider", columns)
        self.assertIn("review_state", columns)
        self.assertIn("review_reason", columns)
        self.assertIn("reviewed_at", columns)
        self.assertEqual(versions, list(range(1, CURRENT_SCHEMA_VERSION + 1)))
        self.assertIn("idx_bookmarks_category", indexes)
        self.assertIn("idx_bookmarks_created_at", indexes)
        self.assertIn("bookmarks_fts", tables)
        self.assertEqual(category_source, "auto")
        self.assertEqual(review_state, "accepted")
        self.assertEqual(search_count, 1)

    def test_new_database_initializes_at_current_schema_version(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")

            version = store.schema_version()

        self.assertEqual(version, CURRENT_SCHEMA_VERSION)

    def test_init_migrates_run_log_metadata_columns(self) -> None:
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
                        category_source TEXT NOT NULL DEFAULT 'auto',
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        confidence REAL,
                        reason TEXT,
                        notes TEXT,
                        read_state TEXT NOT NULL DEFAULT 'unread',
                        important INTEGER NOT NULL DEFAULT 0,
                        archived INTEGER NOT NULL DEFAULT 0,
                        export_path TEXT,
                        imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE sync_state (
                        name TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO schema_migrations(version) VALUES (6);
                    CREATE TABLE run_logs (
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
                )

            store = BookmarkStore(db)
            store.init()

            with sqlite3.connect(db) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(run_logs)").fetchall()
                }
                version = conn.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]

        self.assertEqual(version, CURRENT_SCHEMA_VERSION)
        self.assertIn("connector", columns)
        self.assertIn("cursor_before", columns)
        self.assertIn("cursor_after", columns)
        self.assertIn("inserted_count", columns)
        self.assertIn("has_more", columns)

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
        self.assertEqual(logs[0]["connector"], "json-file")
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
        self.assertIn("connector=json-file", status_output.getvalue())
        self.assertIn("inserted=3", status_output.getvalue())
        self.assertIn("1\tsucceeded\trun", log_output.getvalue())
        self.assertIn("imported=3", log_output.getvalue())
        self.assertIn("inserted=3", log_output.getvalue())

    def test_sync_status_service_returns_structured_summary(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sample = root / "samples" / "bookmarks.json"

        with TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "bookmarks.sqlite"
            main(["--db", str(db), "run", "--input", str(sample)])

            status = BookmarkService(BookmarkStore(db)).sync_status()

        self.assertEqual(status["summary"]["status"], "succeeded")
        self.assertEqual(status["summary"]["connector"], "json-file")
        self.assertEqual(status["summary"]["inserted"], 3)
        self.assertEqual(status["summary"]["source_count"], 3)
        self.assertFalse(status["summary"]["has_more"])


class ConnectorTest(unittest.TestCase):
    def test_build_json_file_connector(self) -> None:
        with TemporaryDirectory() as temp_dir:
            sample = Path(temp_dir) / "bookmarks.json"
            sample.write_text(
                json.dumps([{"id": "9001", "text": "Python programming"}]),
                encoding="utf-8",
            )

            connector = build_connector(
                ConnectorOptions(name="json-file", input_path=sample)
            )
            batch = connector.sync()

        self.assertIsInstance(connector, JsonFileConnector)
        self.assertEqual(len(batch.bookmarks), 1)
        self.assertEqual(batch.bookmarks[0].tweet_id, "9001")
        self.assertEqual(batch.metadata["source_path"], str(sample))

    def test_build_xarchive_json_connector(self) -> None:
        with TemporaryDirectory() as temp_dir:
            sample = Path(temp_dir) / "xarchive.json"
            sample.write_text(
                json.dumps(
                    {
                        "export_metadata": {"tool": "xarchive"},
                        "bookmarks": [
                            {
                                "tweet_id": "9001",
                                "status": "available",
                                "full_text": "VMware note",
                                "author": {"screen_name": "vmw_notes"},
                            },
                            {
                                "tweet_id": "9002",
                                "status": "unavailable",
                                "unavailable_reason": "deleted",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            connector = build_connector(
                ConnectorOptions(name="xarchive-json", input_path=sample)
            )
            batch = connector.sync()

        self.assertIsInstance(connector, XArchiveJsonConnector)
        self.assertEqual([bookmark.tweet_id for bookmark in batch.bookmarks], ["9001"])
        self.assertEqual(batch.bookmarks[0].author, "vmw_notes")
        self.assertEqual(batch.metadata["result_count"], "1")

    def test_read_bearer_token_from_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.txt"
            token_file.write_text("  test-token\n", encoding="utf-8")

            token = read_bearer_token(env_name="MISSING_X_TOKEN", token_file=token_file)

        self.assertEqual(token, "test-token")

    def test_build_x_api_connector_requires_user_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "--x-user-id"):
            build_connector(
                ConnectorOptions(
                    name="x-api",
                    x_bearer_token_env="MISSING_X_TOKEN",
                )
            )

    def test_build_x_api_connector_uses_token_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.txt"
            token_file.write_text("test-token", encoding="utf-8")

            connector = build_connector(
                ConnectorOptions(
                    name="x-api",
                    x_user_id="12345",
                    x_token_file=token_file,
                )
            )

        self.assertIsInstance(connector, XApiConnector)

    def test_x_api_connector_reuses_client_and_paginates(self) -> None:
        client = FakeXApiClient(
            [
                {
                    "data": [
                        {
                            "id": "1001",
                            "text": "VMware note",
                            "author_id": "42",
                            "created_at": "2026-08-11T01:00:00Z",
                        }
                    ],
                    "includes": {
                        "users": [
                            {"id": "42", "username": "xdev", "name": "X Dev"}
                        ]
                    },
                    "meta": {"next_token": "NEXT", "result_count": 1},
                },
                {
                    "data": [
                        {
                            "id": "1002",
                            "text": "Kubernetes note",
                            "author_id": "43",
                        }
                    ],
                    "meta": {"result_count": 1},
                },
            ]
        )
        connector = XApiConnector(
            user_id="12345",
            credential_manager=FakeCredentialManager("ignored"),
            page_size=2,
            max_pages=5,
            client=client,
        )

        batch = connector.sync()

        self.assertEqual([bookmark.tweet_id for bookmark in batch.bookmarks], ["1001", "1002"])
        self.assertEqual(batch.bookmarks[0].author, "xdev")
        self.assertEqual(batch.next_cursor, "tweet:1001")
        self.assertEqual(batch.metadata["pages_fetched"], "2")
        self.assertEqual(
            client.calls,
            [
                {
                    "user_id": "12345",
                    "bearer_token": "ignored",
                    "max_results": 2,
                    "pagination_token": None,
                },
                {
                    "user_id": "12345",
                    "bearer_token": "ignored",
                    "max_results": 2,
                    "pagination_token": "NEXT",
                },
            ],
        )

    def test_x_api_connector_keeps_cursor_when_page_limit_stops_early(self) -> None:
        client = FakeXApiClient(
            [
                {
                    "data": [{"id": "1001", "text": "VMware note"}],
                    "meta": {"next_token": "NEXT", "result_count": 1},
                }
            ]
        )
        connector = XApiConnector(
            user_id="12345",
            credential_manager=FakeCredentialManager("ignored"),
            page_size=10,
            max_pages=1,
            client=client,
        )

        batch = connector.sync(cursor="START")

        self.assertEqual(batch.next_cursor, "page:NEXT")
        self.assertEqual(batch.metadata["has_more"], "true")
        self.assertEqual(client.calls[0]["pagination_token"], "START")

    def test_x_api_connector_stops_when_incremental_cursor_is_reached(self) -> None:
        client = FakeXApiClient(
            [
                {
                    "data": [
                        {"id": "1003", "text": "New VMware note"},
                        {"id": "1002", "text": "Previously seen note"},
                        {"id": "1001", "text": "Older note"},
                    ],
                    "meta": {"result_count": 3},
                }
            ]
        )
        connector = XApiConnector(
            user_id="12345",
            credential_manager=FakeCredentialManager("ignored"),
            page_size=10,
            max_pages=5,
            client=client,
        )

        batch = connector.sync(cursor="tweet:1002")

        self.assertEqual([bookmark.tweet_id for bookmark in batch.bookmarks], ["1003"])
        self.assertEqual(batch.next_cursor, "tweet:1003")
        self.assertEqual(batch.metadata["reached_cursor"], "true")
        self.assertEqual(batch.metadata["has_more"], "false")

    def test_x_api_connector_identifies_auth_errors(self) -> None:
        connector = XApiConnector(
            user_id="12345",
            credential_manager=FakeCredentialManager("ignored"),
            client=FakeXApiClient(
                [
                    {
                        "errors": [
                            {
                                "status": 401,
                                "title": "Unauthorized",
                                "detail": "Invalid token",
                            }
                        ]
                    }
                ]
            ),
        )

        with self.assertRaises(ConnectorAuthError):
            connector.sync()

    def test_x_api_connector_identifies_rate_limit_errors(self) -> None:
        connector = XApiConnector(
            user_id="12345",
            credential_manager=FakeCredentialManager("ignored"),
            client=FakeXApiClient(
                [
                    {
                        "errors": [
                            {
                                "status": 429,
                                "title": "Too Many Requests",
                            }
                        ]
                    }
                ]
            ),
        )

        with self.assertRaises(ConnectorRateLimitError):
            connector.sync()

    def test_x_api_connector_refreshes_token_after_auth_error(self) -> None:
        client = FakeXApiClient(
            [
                ConnectorAuthError("expired token"),
                {"data": [{"id": "1001", "text": "VMware note"}], "meta": {}},
            ]
        )
        credential_manager = FakeCredentialManager("old-token", refreshed="new-token")
        connector = XApiConnector(
            user_id="12345",
            credential_manager=credential_manager,
            client=client,
        )

        batch = connector.sync()

        self.assertEqual(batch.bookmarks[0].tweet_id, "1001")
        self.assertEqual(credential_manager.refresh_count, 1)
        self.assertEqual(client.calls[0]["bearer_token"], "old-token")
        self.assertEqual(client.calls[1]["bearer_token"], "new-token")

    def test_secret_store_writes_oauth_file_with_private_permissions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            secret_path = Path(temp_dir) / "x-oauth.json"

            SecretStore(secret_path).save_oauth(
                access_token="access",
                refresh_token="refresh",
                client_id="client",
            )
            mode = os.stat(secret_path).st_mode & 0o777
            secrets = SecretStore(secret_path).load_oauth()

        self.assertEqual(mode, 0o600)
        self.assertEqual(secrets.access_token, "access")
        self.assertEqual(secrets.refresh_token, "refresh")
        self.assertEqual(secrets.client_id, "client")

    def test_x_oauth_store_command_reads_token_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            db = base / "bookmarks.sqlite"
            secret_path = base / "x-oauth.json"
            access_file = base / "access.txt"
            refresh_file = base / "refresh.txt"
            access_file.write_text("access\n", encoding="utf-8")
            refresh_file.write_text("refresh\n", encoding="utf-8")

            exit_code = main(
                [
                    "--db",
                    str(db),
                    "x-oauth",
                    "store",
                    "--secret-path",
                    str(secret_path),
                    "--client-id",
                    "client",
                    "--access-token-file",
                    str(access_file),
                    "--refresh-token-file",
                    str(refresh_file),
                ]
            )
            secrets = SecretStore(secret_path).load_oauth()

        self.assertEqual(exit_code, 0)
        self.assertEqual(secrets.access_token, "access")
        self.assertEqual(secrets.refresh_token, "refresh")
        self.assertEqual(secrets.client_id, "client")

    def test_x_api_sync_requires_capability_check(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "bookmarks.sqlite"
            secret_path = Path(temp_dir) / "x-oauth.json"
            SecretStore(secret_path).save_oauth(access_token="access")

            with self.assertRaises(ConnectorCapabilityError):
                main(
                    [
                        "--db",
                        str(db),
                        "import",
                        "--connector",
                        "x-api",
                        "--x-user-id",
                        "12345",
                        "--x-secret-path",
                        str(secret_path),
                    ]
                )


class FakeXApiClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get_bookmarks(
        self,
        bearer_token: str,
        user_id: str,
        max_results: int,
        pagination_token: str | None = None,
    ) -> dict:
        self.calls.append(
            {
                "user_id": user_id,
                "bearer_token": bearer_token,
                "max_results": max_results,
                "pagination_token": pagination_token,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeCredentialManager:
    def __init__(self, token: str, refreshed: str | None = None) -> None:
        self.token = token
        self.refreshed = refreshed or token
        self.refresh_count = 0

    def access_token(self) -> str:
        return self.token

    def refresh_access_token(self, client: object) -> str:
        del client
        self.refresh_count += 1
        return self.refreshed


def _read_json_url(
    url: str, *, method: str = "GET", payload: dict | None = None
) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
