from __future__ import annotations

import argparse
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
from .exporter import export_html
from .importer import load_bookmarks
from .models import ClassificationResult
from .providers import (
    PROVIDER_NAMES,
    ProviderOptions,
    build_ollama_healthcheck_provider,
    build_provider,
)
from .storage import BookmarkStore


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
    import_parser.add_argument("input", type=Path)

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

    export_parser = subparsers.add_parser("export-html")
    export_parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input", type=Path, required=True)
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

    subparsers.add_parser("sync-status")

    run_log_parser = subparsers.add_parser("run-log")
    run_log_parser.add_argument("--limit", type=int, default=10)

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
        bookmarks = load_bookmarks(args.input)
        result = store.upsert_bookmarks(bookmarks)
        print(
            f"Imported {result.imported} bookmark(s): "
            f"inserted={result.inserted} updated={result.updated} "
            f"unchanged={result.unchanged} duplicates={result.duplicates}"
        )
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
        store.save_classification(
            args.tweet_id,
            ClassificationResult(
                category=args.category,
                tags=tags,
                confidence=1.0,
                reason=args.reason,
            ),
            source="manual",
        )
        if args.archive_dir:
            export_html(store, args.archive_dir)
        print(f"Updated category for {args.tweet_id}: {args.category}")
        return 0

    if args.command == "export-html":
        count = export_html(store, args.archive_dir)
        print(f"Exported {count} bookmark HTML file(s) to {args.archive_dir}.")
        return 0

    if args.command == "run":
        provider_instance = _build_provider(
            args.provider,
            load_category_config(args.categories),
            args.ollama_model,
            args.ollama_url,
            args.ollama_timeout,
        )
        run_id = store.begin_run(
            "run",
            input_path=args.input,
            archive_dir=args.archive_dir,
            provider=provider_instance.name,
            model=provider_instance.model_label,
        )
        imported = 0
        import_summary = None
        classified = 0
        exported = 0
        try:
            bookmarks = load_bookmarks(args.input)
            import_summary = store.upsert_bookmarks(bookmarks)
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
                message=str(exc),
            )
            raise
        store.finish_run(
            run_id,
            "succeeded",
            imported_count=imported,
            classified_count=classified,
            exported_count=exported,
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
                f"started_at={run['started_at']} ended_at={run['ended_at']}"
            )
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
                f"imported={row['imported_count']}\t"
                f"classified={row['classified_count']}\t"
                f"exported={row['exported_count']}\t"
                f"message={row['message'] or ''}"
            )
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
        store.save_classification(row["tweet_id"], result)
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
