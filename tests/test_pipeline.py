from __future__ import annotations

import json
import os
import sqlite3
import threading
import unittest
from contextlib import redirect_stdout
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
    build_connector,
    read_bearer_token,
)
from xbookmarks.models import Bookmark, ClassificationResult
from xbookmarks.secrets import SecretStore
from xbookmarks.storage import BookmarkStore
from xbookmarks.web import XBookmarksHandler


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
            by_query = store.list_bookmarks(query="follow")

        self.assertEqual([row["tweet_id"] for row in by_category], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_read], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_important], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_archived], ["9001"])
        self.assertEqual([row["tweet_id"] for row in by_query], ["9001"])

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
                bookmark_store = store

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
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(list_body["items"][0]["tweet_id"], "9001")
        self.assertEqual(patch_body["item"]["category"], "VCF")
        self.assertEqual(patch_body["item"]["read_state"], "read")
        self.assertTrue(patch_body["item"]["important"])
        self.assertEqual(filtered_body["items"][0]["tweet_id"], "9001")


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
                search_count = conn.execute(
                    "SELECT COUNT(*) FROM bookmarks_fts WHERE bookmarks_fts MATCH 'VMware'"
                ).fetchone()[0]

        self.assertIn("category_source", columns)
        self.assertIn("notes", columns)
        self.assertIn("read_state", columns)
        self.assertIn("important", columns)
        self.assertIn("archived", columns)
        self.assertEqual(versions, [1, 2, 3, 4, 5, 6])
        self.assertIn("idx_bookmarks_category", indexes)
        self.assertIn("idx_bookmarks_created_at", indexes)
        self.assertIn("bookmarks_fts", tables)
        self.assertEqual(category_source, "auto")
        self.assertEqual(search_count, 1)

    def test_new_database_initializes_at_current_schema_version(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BookmarkStore(Path(temp_dir) / "bookmarks.sqlite")

            version = store.schema_version()

        self.assertEqual(version, 6)

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
        self.assertIsNone(batch.next_cursor)
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

        self.assertEqual(batch.next_cursor, "NEXT")
        self.assertEqual(batch.metadata["has_more"], "true")
        self.assertEqual(client.calls[0]["pagination_token"], "START")

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
