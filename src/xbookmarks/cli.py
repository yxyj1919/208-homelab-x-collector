from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .config import (
    DEFAULT_CATEGORIES,
    INTEREST_PRESETS,
    CategoryDefinition,
    category_config_for_interests,
    load_category_config,
    merge_category_config,
    merge_category_rules,
    save_category_config,
)
from .connectors import (
    CONNECTOR_NAMES,
    ConnectorCapabilityError,
    ConnectorOptions,
    SyncBatch,
    build_connector,
)
from .exporter import export_html, export_markdown
from .models import ClassificationResult
from .providers import (
    PROVIDER_NAMES,
    ProviderOptions,
    build_ollama_healthcheck_provider,
    build_provider,
)
from .secrets import DEFAULT_SECRET_PATH, SecretStore
from .services import BookmarkService
from .storage import BookmarkStore
from .web import run_web_server


DEFAULT_DB = Path("data/bookmarks.sqlite")
DEFAULT_ARCHIVE = Path("archive")
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_TIMEOUT = 180


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xbookmarks")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--categories", type=Path, default=DEFAULT_CATEGORIES)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument(
        "--interests",
        help=(
            "Comma-separated first-use interest presets. Available: "
            + ", ".join(sorted(INTEREST_PRESETS))
        ),
    )
    init_parser.add_argument(
        "--write-categories",
        action="store_true",
        help="Create or update the category config with selected presets.",
    )
    init_parser.add_argument(
        "--force-categories",
        action="store_true",
        help="Overwrite the category config when writing presets.",
    )

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("input", type=Path, nargs="?")
    import_parser.add_argument(
        "--connector", choices=CONNECTOR_NAMES, default="json-file"
    )
    _add_connector_args(import_parser)

    capability_parser = subparsers.add_parser("capability-check")
    capability_parser.add_argument("input", type=Path, nargs="?")
    capability_parser.add_argument("--connector", choices=CONNECTOR_NAMES, default="x-api")
    _add_connector_args(capability_parser)

    x_oauth_parser = subparsers.add_parser("x-oauth")
    x_oauth_subparsers = x_oauth_parser.add_subparsers(
        dest="x_oauth_command", required=True
    )
    x_oauth_store_parser = x_oauth_subparsers.add_parser("store")
    x_oauth_store_parser.add_argument("--secret-path", type=Path, default=DEFAULT_SECRET_PATH)
    x_oauth_store_parser.add_argument("--client-id")
    x_oauth_store_parser.add_argument("--access-token")
    x_oauth_store_parser.add_argument("--access-token-file", type=Path)
    x_oauth_store_parser.add_argument("--refresh-token")
    x_oauth_store_parser.add_argument("--refresh-token-file", type=Path)

    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("--all", action="store_true", help="Reclassify all rows")
    classify_parser.add_argument("--only-category")
    classify_parser.add_argument("--limit", type=int)
    classify_parser.add_argument(
        "--include-manual",
        action="store_true",
        help="Allow reclassification to overwrite manually adjusted categories.",
    )
    _add_provider_args(classify_parser)

    benchmark_parser = subparsers.add_parser("benchmark")
    benchmark_parser.add_argument("--limit", type=int, default=5)
    benchmark_parser.add_argument("--only-category")
    _add_provider_args(benchmark_parser)

    ollama_check_parser = subparsers.add_parser("ollama-check")
    _add_ollama_args(ollama_check_parser)

    set_category_parser = subparsers.add_parser("set-category")
    set_category_parser.add_argument("tweet_id")
    set_category_parser.add_argument("category")
    set_category_parser.add_argument("--tags", default="")
    set_category_parser.add_argument(
        "--reason", default="Manually adjusted by user."
    )
    set_category_parser.add_argument(
        "--archive-dir",
        type=Path,
        help="Re-export HTML archive after changing the category.",
    )

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("tweet_id")
    update_parser.add_argument("--category")
    update_parser.add_argument("--tags")
    update_parser.add_argument("--notes")
    update_parser.add_argument("--read-state", choices=("read", "unread"))
    update_parser.add_argument(
        "--important",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    update_parser.add_argument(
        "--archived",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    update_parser.add_argument(
        "--archive-dir",
        type=Path,
        help="Re-export HTML archive after updating bookmark metadata.",
    )

    export_parser = subparsers.add_parser("export-html")
    export_parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)

    export_markdown_parser = subparsers.add_parser("export-markdown")
    export_markdown_parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input", type=Path)
    run_parser.add_argument(
        "--connector", choices=CONNECTOR_NAMES, default="json-file"
    )
    _add_connector_args(run_parser)
    run_parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    run_parser.add_argument("--reclassify", action="store_true")
    run_parser.add_argument(
        "--include-manual",
        action="store_true",
        help="Allow reclassification to overwrite manually adjusted categories.",
    )
    _add_provider_args(run_parser)

    category_parser = subparsers.add_parser("category")
    category_subparsers = category_parser.add_subparsers(
        dest="category_command", required=True
    )
    category_subparsers.add_parser("list")
    category_subparsers.add_parser("presets")
    category_add_parser = category_subparsers.add_parser("add")
    category_add_parser.add_argument("name")
    category_add_parser.add_argument("--description", default="")
    category_add_parser.add_argument("--keyword", action="append", default=[])
    category_add_parser.add_argument(
        "--keywords",
        default="",
        help="Comma-separated keywords to add.",
    )
    category_add_parser.add_argument("--replace", action="store_true")

    stats_parser = subparsers.add_parser("stats")
    stats_parser.add_argument("--by-category", action="store_true")

    subparsers.add_parser("review-summary")

    subparsers.add_parser("sync-status")

    run_log_parser = subparsers.add_parser("run-log")
    run_log_parser.add_argument("--limit", type=int, default=10)

    web_parser = subparsers.add_parser("web")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    store = BookmarkStore(args.db)

    if args.command == "init":
        store.init()
        if args.write_categories:
            _write_initial_categories(args.categories, args.interests, args.force_categories)
        print(f"Initialized database: {args.db}")
        if args.write_categories:
            print(f"Updated category config: {args.categories}")
        return 0

    if args.command == "import":
        batch = _sync_bookmarks(
            store,
            connector_name=args.connector,
            input_path=args.input,
            x_user_id=args.x_user_id,
            x_bearer_token_env=args.x_bearer_token_env,
            x_token_file=args.x_token_file,
            x_secret_path=args.x_secret_path,
            x_api_base_url=args.x_api_base_url,
            x_page_size=args.x_page_size,
            x_max_pages=args.x_max_pages,
        )
        result = store.upsert_bookmarks(batch.bookmarks)
        _apply_xarchive_metadata(store, batch)
        print(
            f"Imported {result.imported} bookmark(s): "
            f"inserted={result.inserted} updated={result.updated} "
            f"unchanged={result.unchanged} duplicates={result.duplicates}"
        )
        return 0

    if args.command == "capability-check":
        batch = _capability_check(
            store,
            connector_name=args.connector,
            input_path=args.input,
            x_user_id=args.x_user_id,
            x_bearer_token_env=args.x_bearer_token_env,
            x_token_file=args.x_token_file,
            x_secret_path=args.x_secret_path,
            x_api_base_url=args.x_api_base_url,
            x_page_size=args.x_page_size,
            x_max_pages=args.x_max_pages,
        )
        print(
            f"Capability check succeeded: connector={args.connector} "
            f"status={batch.metadata.get('status', 'ok')}"
        )
        return 0

    if args.command == "x-oauth":
        if args.x_oauth_command == "store":
            access_token = _secret_arg(args.access_token, args.access_token_file)
            refresh_token = _secret_arg(args.refresh_token, args.refresh_token_file)
            if not (args.client_id or access_token or refresh_token):
                parser.error(
                    "x-oauth store requires at least one of --client-id, "
                    "--access-token-file, --access-token, --refresh-token-file, "
                    "or --refresh-token"
                )
            SecretStore(args.secret_path).save_oauth(
                access_token=access_token,
                refresh_token=refresh_token,
                client_id=args.client_id,
            )
            print(f"Stored X OAuth secrets: {args.secret_path}")
            return 0

    if args.command == "classify":
        count = _classify(
            store,
            categories_path=args.categories,
            provider=args.provider,
            ollama_model=args.ollama_model,
            ollama_url=args.ollama_url,
            ollama_timeout=args.ollama_timeout,
            only_unclassified=(not args.all and args.only_category is None),
            only_category=args.only_category,
            limit=args.limit,
            include_manual=args.include_manual,
        )
        print(f"Classified {count} bookmark(s).")
        return 0

    if args.command == "benchmark":
        result = _benchmark(
            store,
            categories_path=args.categories,
            provider=args.provider,
            ollama_model=args.ollama_model,
            ollama_url=args.ollama_url,
            ollama_timeout=args.ollama_timeout,
            only_category=args.only_category,
            limit=args.limit,
        )
        print(
            f"Benchmarked {result['count']} bookmark(s): "
            f"total_seconds={result['total_seconds']:.2f} "
            f"avg_seconds={result['avg_seconds']:.2f} "
            f"provider={result['provider']} model={result['model']}"
        )
        return 0

    if args.command == "ollama-check":
        classifier = build_ollama_healthcheck_provider(
            model=args.ollama_model,
            base_url=args.ollama_url,
            timeout_seconds=args.ollama_timeout,
        )
        models = classifier.check()
        if args.ollama_model in models:
            status = "available"
        else:
            status = "missing"
        print(
            f"Ollama reachable: url={args.ollama_url} "
            f"model={args.ollama_model} status={status}"
        )
        if models:
            print("Models:")
            for model in models:
                print(f"- {model}")
        return 0

    if args.command == "set-category":
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
        BookmarkService(store).set_bookmark_category(
            args.tweet_id,
            args.category,
            tags=tags,
            reason=args.reason,
        )
        if args.archive_dir:
            export_html(store, args.archive_dir)
        print(f"Updated category for {args.tweet_id}: {args.category}")
        return 0

    if args.command == "update":
        payload = _bookmark_update_payload(args)
        if not payload:
            parser.error(
                "update requires at least one of --category, --tags, --notes, "
                "--read-state, --important/--no-important, or "
                "--archived/--no-archived"
            )
        response = BookmarkService(store).update_bookmark(args.tweet_id, payload)
        if args.archive_dir:
            export_html(store, args.archive_dir)
        item = response["item"] or {}
        print(
            f"Updated bookmark {args.tweet_id}: "
            f"category={item.get('category', '')} "
            f"read_state={item.get('read_state', '')} "
            f"important={item.get('important', '')} "
            f"archived={item.get('archived', '')}"
        )
        return 0

    if args.command == "export-html":
        count = export_html(store, args.archive_dir)
        print(f"Exported {count} bookmark HTML file(s) to {args.archive_dir}.")
        return 0

    if args.command == "export-markdown":
        count = export_markdown(store, args.archive_dir)
        print(f"Exported {count} bookmark Markdown file(s) to {args.archive_dir}.")
        return 0

    if args.command == "search":
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        rows = store.search_bookmarks(args.query, limit=args.limit)
        if not rows:
            print("No matching bookmarks.")
            return 0
        for row in rows:
            tags = ", ".join(_json_list(row["tags_json"]))
            text = _one_line(row["text"], max_length=120)
            print(
                f"{row['tweet_id']}\t"
                f"category={row['category'] or 'Unclassified'}\t"
                f"author={row['author'] or ''}\t"
                f"tags={tags}\t"
                f"url={row['url']}\t"
                f"text={text}"
            )
        return 0

    if args.command == "run":
        provider_instance = _build_provider(
            args.provider,
            load_category_config(args.categories),
            args.ollama_model,
            args.ollama_url,
            args.ollama_timeout,
        )
        cursor_before = store.sync_state().get(f"{args.connector}.cursor")
        run_id = store.begin_run(
            "run",
            input_path=args.input,
            archive_dir=args.archive_dir,
            connector=args.connector,
            cursor_before=cursor_before,
            provider=provider_instance.name,
            model=provider_instance.model_label,
        )
        imported = 0
        import_summary = None
        classified = 0
        exported = 0
        batch: SyncBatch | None = None
        try:
            batch = _sync_bookmarks(
                store,
                connector_name=args.connector,
                input_path=args.input,
                x_user_id=args.x_user_id,
                x_bearer_token_env=args.x_bearer_token_env,
                x_token_file=args.x_token_file,
                x_secret_path=args.x_secret_path,
                x_api_base_url=args.x_api_base_url,
                x_page_size=args.x_page_size,
                x_max_pages=args.x_max_pages,
            )
            import_summary = store.upsert_bookmarks(batch.bookmarks)
            _apply_xarchive_metadata(store, batch)
            imported = import_summary.imported
            classified = _classify(
                store,
                categories_path=args.categories,
                provider=args.provider,
                ollama_model=args.ollama_model,
                ollama_url=args.ollama_url,
                ollama_timeout=args.ollama_timeout,
                only_unclassified=not args.reclassify,
                only_category=None,
                limit=None,
                include_manual=args.include_manual,
            )
            exported = export_html(store, args.archive_dir)
        except Exception as exc:
            store.finish_run(
                run_id,
                "failed",
                imported_count=imported,
                classified_count=classified,
                exported_count=exported,
                **_run_log_sync_fields(batch, import_summary),
                message=str(exc),
            )
            raise
        store.finish_run(
            run_id,
            "succeeded",
            imported_count=imported,
            classified_count=classified,
            exported_count=exported,
            **_run_log_sync_fields(batch, import_summary),
        )
        import_details = ""
        if import_summary is not None:
            import_details = (
                f", inserted={import_summary.inserted}"
                f", updated={import_summary.updated}"
                f", unchanged={import_summary.unchanged}"
                f", duplicates={import_summary.duplicates}"
            )
        print(
            f"Run complete: run_id={run_id}, imported={imported}{import_details}, classified={classified}, "
            f"exported={exported}, archive={args.archive_dir}"
        )
        return 0

    if args.command == "sync-status":
        state = store.sync_state()
        if not state:
            print("No sync state recorded.")
            return 0
        for name, value in state.items():
            print(f"{name}={value}")
        latest = store.list_run_logs(limit=1)
        if latest:
            run = latest[0]
            print(
                "latest_run="
                f"id={run['id']} status={run['status']} command={run['command']} "
                f"connector={run['connector'] or ''} "
                f"imported={run['imported_count']} "
                f"inserted={run['inserted_count']} "
                f"updated={run['updated_count']} "
                f"unchanged={run['unchanged_count']} "
                f"duplicates={run['duplicate_count']} "
                f"pages={run['pages_fetched']} has_more={bool(run['has_more'])} "
                f"started_at={run['started_at']} ended_at={run['ended_at']}"
            )
        return 0

    if args.command == "review-summary":
        summary = BookmarkService(store).review_summary()
        print(
            f"total={summary['total']} pending={summary['pending']} "
            f"accepted={summary['accepted']}"
        )
        by_reason = summary.get("by_reason") or {}
        if by_reason:
            for reason, count in sorted(by_reason.items()):
                print(f"{reason}\t{count}")
        return 0

    if args.command == "run-log":
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        rows = store.list_run_logs(limit=args.limit)
        if not rows:
            print("No run logs recorded.")
            return 0
        for row in rows:
            print(
                f"{row['id']}\t{row['status']}\t{row['command']}\t"
                f"started={row['started_at']}\tended={row['ended_at']}\t"
                f"provider={row['provider'] or ''}\tmodel={row['model'] or ''}\t"
                f"connector={row['connector'] or ''}\t"
                f"pages={row['pages_fetched']}\t"
                f"source={row['source_count']}\t"
                f"imported={row['imported_count']}\t"
                f"inserted={row['inserted_count']}\t"
                f"updated={row['updated_count']}\t"
                f"unchanged={row['unchanged_count']}\t"
                f"duplicates={row['duplicate_count']}\t"
                f"classified={row['classified_count']}\t"
                f"exported={row['exported_count']}\t"
                f"has_more={bool(row['has_more'])}\t"
                f"message={row['message'] or ''}"
            )
        return 0

    if args.command == "web":
        if args.port < 1 or args.port > 65535:
            parser.error("--port must be between 1 and 65535")
        run_web_server(args.db, host=args.host, port=args.port)
        return 0

    if args.command == "stats":
        stats = store.stats()
        print(
            f"total={stats['total']} classified={stats['classified']} "
            f"exported={stats['exported']}"
        )
        if args.by_category:
            for category, count in store.category_counts():
                print(f"{category}\t{count}")
        return 0

    if args.command == "category":
        if args.category_command == "list":
            definitions = load_category_config(args.categories)
            for category, definition in definitions.items():
                print(
                    f"{category}\t{definition.description}\t"
                    f"{', '.join(definition.keywords)}"
                )
            return 0
        if args.category_command == "presets":
            for interest, definitions in sorted(INTEREST_PRESETS.items()):
                print(f"{interest}\t{', '.join(definitions)}")
            return 0
        if args.category_command == "add":
            definitions = _load_category_config_or_empty(args.categories)
            keywords = args.keyword + _parse_csv(args.keywords)
            if args.name in definitions and not args.replace:
                current = definitions[args.name]
                merged_rules = merge_category_rules(
                    {args.name: current.keywords}, {args.name: keywords}
                )
                definitions[args.name] = CategoryDefinition(
                    description=args.description or current.description,
                    keywords=merged_rules[args.name],
                )
            else:
                definitions[args.name] = CategoryDefinition(
                    description=args.description, keywords=keywords
                )
            save_category_config(definitions, args.categories)
            print(f"Saved category {args.name} to {args.categories}")
            return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _add_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=PROVIDER_NAMES, default="rules")
    _add_ollama_args(parser)


def _add_ollama_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ollama-model", default=_env("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL))
    parser.add_argument("--ollama-url", default=_env("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL))
    parser.add_argument(
        "--ollama-timeout",
        type=int,
        default=_env_int("OLLAMA_TIMEOUT", DEFAULT_OLLAMA_TIMEOUT),
    )


def _add_connector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--x-user-id")
    parser.add_argument("--x-bearer-token-env", default="X_BEARER_TOKEN")
    parser.add_argument("--x-token-file", type=Path)
    parser.add_argument("--x-secret-path", type=Path, default=DEFAULT_SECRET_PATH)
    parser.add_argument("--x-api-base-url", default="https://api.x.com")
    parser.add_argument("--x-page-size", type=int, default=100)
    parser.add_argument("--x-max-pages", type=int, default=10)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _classify(
    store: BookmarkStore,
    categories_path: Path,
    provider: str,
    ollama_model: str,
    ollama_url: str,
    ollama_timeout: int,
    only_unclassified: bool,
    only_category: str | None,
    limit: int | None,
    include_manual: bool,
) -> int:
    definitions = load_category_config(categories_path)
    provider_instance = _build_provider(
        provider, definitions, ollama_model, ollama_url, ollama_timeout
    )
    rows = store.iter_bookmarks(
        only_unclassified=only_unclassified,
        category=only_category,
        limit=limit,
        skip_manual=not include_manual,
    )
    total = len(rows)
    for index, row in enumerate(rows, 1):
        if provider_instance.show_progress:
            print(
                f"Classifying {index}/{total}: {row['tweet_id']}",
                file=sys.stderr,
                flush=True,
            )
        result = provider_instance.classifier.classify(
            f"{row.get('text') or ''} {row.get('author') or ''}"
        )
        store.save_classification(
            row["tweet_id"], result, provider=provider_instance.name
        )
    return len(rows)


def _benchmark(
    store: BookmarkStore,
    categories_path: Path,
    provider: str,
    ollama_model: str,
    ollama_url: str,
    ollama_timeout: int,
    only_category: str | None,
    limit: int,
) -> dict[str, float | int | str]:
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    definitions = load_category_config(categories_path)
    provider_instance = _build_provider(
        provider, definitions, ollama_model, ollama_url, ollama_timeout
    )
    rows = store.iter_bookmarks(
        only_unclassified=False,
        category=only_category,
        limit=limit,
    )
    start = time.perf_counter()
    for index, row in enumerate(rows, 1):
        if provider_instance.show_progress:
            print(
                f"Benchmarking {index}/{len(rows)}: {row['tweet_id']}",
                file=sys.stderr,
                flush=True,
            )
        provider_instance.classifier.classify(
            f"{row.get('text') or ''} {row.get('author') or ''}"
        )
    elapsed = time.perf_counter() - start
    count = len(rows)
    return {
        "count": count,
        "total_seconds": elapsed,
        "avg_seconds": elapsed / count if count else 0.0,
        "provider": provider_instance.name,
        "model": provider_instance.model_label,
    }


def _write_initial_categories(
    categories_path: Path, interests: str | None, force: bool
) -> None:
    selected = _parse_csv(interests)
    if not selected:
        selected = ["virtualization", "kubernetes", "homelab", "ai", "security"]
    preset_definitions = category_config_for_interests(selected)

    if categories_path.exists() and not force:
        existing = load_category_config(categories_path)
        definitions = merge_category_config(existing, preset_definitions)
    else:
        definitions = preset_definitions
    if "General" not in definitions:
        definitions["General"] = CategoryDefinition(
            description="Fallback category when no specific interest category fits.",
            keywords=[],
        )
    save_category_config(definitions, categories_path)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _bookmark_update_payload(args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {}
    if args.category is not None:
        payload["category"] = args.category
    if args.tags is not None:
        payload["tags"] = _parse_csv(args.tags)
    if args.notes is not None:
        payload["notes"] = args.notes
    if args.read_state is not None:
        payload["read_state"] = args.read_state
    if args.important is not None:
        payload["important"] = args.important
    if args.archived is not None:
        payload["archived"] = args.archived
    return payload


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return [value]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _one_line(value: str | None, max_length: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "..."


def _secret_arg(value: str | None, file_path: Path | None) -> str | None:
    if value and file_path:
        raise ValueError("Pass a secret value or a secret file, not both.")
    if file_path is not None:
        text = file_path.read_text(encoding="utf-8").strip()
        return text or None
    if value is None:
        return None
    text = value.strip()
    return text or None


def _sync_bookmarks(
    store: BookmarkStore,
    connector_name: str,
    input_path: Path | None,
    x_user_id: str | None = None,
    x_bearer_token_env: str = "X_BEARER_TOKEN",
    x_token_file: Path | None = None,
    x_secret_path: Path = DEFAULT_SECRET_PATH,
    x_api_base_url: str = "https://api.x.com",
    x_page_size: int = 100,
    x_max_pages: int = 10,
) -> SyncBatch:
    if connector_name == "x-api":
        state = store.sync_state()
        if state.get("x-api.capability.status") != "ok":
            raise ConnectorCapabilityError(
                "Run capability-check with --connector x-api before x-api sync."
            )
    connector = build_connector(
        ConnectorOptions(
            name=connector_name,
            input_path=input_path,
            x_user_id=x_user_id,
            x_bearer_token_env=x_bearer_token_env,
            x_token_file=x_token_file,
            x_secret_path=x_secret_path,
            x_api_base_url=x_api_base_url,
            x_page_size=x_page_size,
            x_max_pages=x_max_pages,
        )
    )
    state = store.sync_state()
    cursor = state.get(f"{connector.name}.cursor")
    batch = connector.sync(cursor=cursor)
    store.set_sync_state("last_connector", connector.name)
    store.set_sync_state(f"{connector.name}.cursor", batch.next_cursor or "")
    for key, value in sorted(batch.metadata.items()):
        store.set_sync_state(f"{connector.name}.{key}", value)
    batch.metadata["connector"] = connector.name
    batch.metadata["cursor_before"] = cursor or ""
    batch.metadata["cursor_after"] = batch.next_cursor or ""
    return batch


def _apply_xarchive_metadata(store: BookmarkStore, batch: SyncBatch) -> int:
    if batch.metadata.get("connector") != "xarchive-json":
        return 0
    updated = 0
    for bookmark in batch.bookmarks:
        row = store.get_bookmark(bookmark.tweet_id)
        if row is None or row.get("category_source") == "manual":
            continue
        folders = _xarchive_folders(bookmark.raw or {})
        if not folders:
            continue
        store.save_classification(
            bookmark.tweet_id,
            ClassificationResult(
                category=folders[0],
                tags=folders,
                confidence=1.0,
                reason="Imported from xarchive bookmark folders.",
            ),
            source="auto",
            provider="xarchive-json",
        )
        updated += 1
    return updated


def _xarchive_folders(raw: dict[str, object]) -> list[str]:
    value = raw.get("folders")
    if not isinstance(value, list):
        return []
    folders: list[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text and text not in folders:
            folders.append(text)
    return folders


def _capability_check(
    store: BookmarkStore,
    connector_name: str,
    input_path: Path | None,
    x_user_id: str | None = None,
    x_bearer_token_env: str = "X_BEARER_TOKEN",
    x_token_file: Path | None = None,
    x_secret_path: Path = DEFAULT_SECRET_PATH,
    x_api_base_url: str = "https://api.x.com",
    x_page_size: int = 100,
    x_max_pages: int = 10,
) -> SyncBatch:
    connector = build_connector(
        ConnectorOptions(
            name=connector_name,
            input_path=input_path,
            x_user_id=x_user_id,
            x_bearer_token_env=x_bearer_token_env,
            x_token_file=x_token_file,
            x_secret_path=x_secret_path,
            x_api_base_url=x_api_base_url,
            x_page_size=x_page_size,
            x_max_pages=x_max_pages,
        )
    )
    if not hasattr(connector, "capability_check"):
        batch = connector.sync(cursor=None)
        metadata = {
            "status": "ok",
            "result_count": str(len(batch.bookmarks)),
        }
        batch = SyncBatch(bookmarks=[], metadata=metadata)
    else:
        batch = connector.capability_check()
    store.set_sync_state("last_connector", connector.name)
    for key, value in sorted(batch.metadata.items()):
        store.set_sync_state(f"{connector.name}.capability.{key}", value)
    return batch


def _run_log_sync_fields(
    batch: SyncBatch | None,
    import_summary: object | None,
) -> dict[str, object]:
    metadata = batch.metadata if batch is not None else {}
    return {
        "cursor_after": metadata.get("cursor_after") or None,
        "pages_fetched": _int_metadata(metadata, "pages_fetched"),
        "source_count": _int_metadata(metadata, "result_count")
        or (len(batch.bookmarks) if batch is not None else 0),
        "inserted_count": getattr(import_summary, "inserted", 0) or 0,
        "updated_count": getattr(import_summary, "updated", 0) or 0,
        "unchanged_count": getattr(import_summary, "unchanged", 0) or 0,
        "duplicate_count": getattr(import_summary, "duplicates", 0) or 0,
        "has_more": str(metadata.get("has_more", "")).lower() == "true",
    }


def _int_metadata(metadata: dict[str, str], name: str) -> int:
    try:
        return int(metadata.get(name, "0") or "0")
    except ValueError:
        return 0


def _load_category_config_or_empty(path: Path) -> dict[str, CategoryDefinition]:
    if not path.exists():
        return {}
    return load_category_config(path)


def _build_classifier(
    provider: str,
    definitions: dict[str, CategoryDefinition],
    ollama_model: str,
    ollama_url: str,
    ollama_timeout: int,
) -> object:
    return _build_provider(
        provider, definitions, ollama_model, ollama_url, ollama_timeout
    ).classifier


def _build_provider(
    provider: str,
    definitions: dict[str, CategoryDefinition],
    ollama_model: str,
    ollama_url: str,
    ollama_timeout: int,
):
    return build_provider(
        ProviderOptions(
            name=provider,
            ollama_model=ollama_model,
            ollama_url=ollama_url,
            ollama_timeout=ollama_timeout,
        ),
        definitions,
    )


if __name__ == "__main__":
    raise SystemExit(main())
