from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .classifier import OllamaClassifier, RuleBasedClassifier
from .config import (
    DEFAULT_CATEGORIES,
    INTEREST_PRESETS,
    CategoryDefinition,
    load_category_config,
    load_category_rules,
    merge_category_rules,
    rules_for_interests,
    save_category_config,
    save_category_rules,
)
from .exporter import export_html
from .importer import load_bookmarks
from .models import ClassificationResult
from .storage import BookmarkStore


DEFAULT_DB = Path("data/bookmarks.sqlite")
DEFAULT_ARCHIVE = Path("archive")


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
    _add_provider_args(classify_parser)

    benchmark_parser = subparsers.add_parser("benchmark")
    benchmark_parser.add_argument("--limit", type=int, default=5)
    benchmark_parser.add_argument("--only-category")
    _add_provider_args(benchmark_parser)

    set_category_parser = subparsers.add_parser("set-category")
    set_category_parser.add_argument("tweet_id")
    set_category_parser.add_argument("category")
    set_category_parser.add_argument("--tags", default="")
    set_category_parser.add_argument(
        "--reason", default="Manually adjusted by user."
    )

    export_parser = subparsers.add_parser("export-html")
    export_parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input", type=Path, required=True)
    run_parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    run_parser.add_argument("--reclassify", action="store_true")
    _add_provider_args(run_parser)

    category_parser = subparsers.add_parser("category")
    category_subparsers = category_parser.add_subparsers(
        dest="category_command", required=True
    )
    category_subparsers.add_parser("list")
    category_add_parser = category_subparsers.add_parser("add")
    category_add_parser.add_argument("name")
    category_add_parser.add_argument("--description", default="")
    category_add_parser.add_argument("--keyword", action="append", default=[])
    category_add_parser.add_argument("--replace", action="store_true")

    stats_parser = subparsers.add_parser("stats")
    stats_parser.add_argument("--by-category", action="store_true")

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
        count = store.upsert_bookmarks(bookmarks)
        print(f"Imported {count} bookmark(s).")
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
            f"provider={args.provider} model={args.ollama_model if args.provider == 'ollama' else 'rules'}"
        )
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
        )
        print(f"Updated category for {args.tweet_id}: {args.category}")
        return 0

    if args.command == "export-html":
        count = export_html(store, args.archive_dir)
        print(f"Exported {count} bookmark HTML file(s) to {args.archive_dir}.")
        return 0

    if args.command == "run":
        bookmarks = load_bookmarks(args.input)
        imported = store.upsert_bookmarks(bookmarks)
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
        )
        exported = export_html(store, args.archive_dir)
        print(
            f"Run complete: imported={imported}, classified={classified}, "
            f"exported={exported}, archive={args.archive_dir}"
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
        if args.category_command == "add":
            definitions = load_category_config(args.categories)
            if args.name in definitions and not args.replace:
                current = definitions[args.name]
                merged_rules = merge_category_rules(
                    {args.name: current.keywords}, {args.name: args.keyword}
                )
                definitions[args.name] = CategoryDefinition(
                    description=args.description or current.description,
                    keywords=merged_rules[args.name],
                )
            else:
                definitions[args.name] = CategoryDefinition(
                    description=args.description, keywords=args.keyword
                )
            save_category_config(definitions, args.categories)
            print(f"Saved category {args.name} to {args.categories}")
            return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _add_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=("rules", "ollama"), default="rules")
    parser.add_argument("--ollama-model", default="qwen2.5:7b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-timeout", type=int, default=180)


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
) -> int:
    definitions = load_category_config(categories_path)
    classifier = _build_classifier(
        provider, definitions, ollama_model, ollama_url, ollama_timeout
    )
    rows = store.iter_bookmarks(
        only_unclassified=only_unclassified, category=only_category, limit=limit
    )
    total = len(rows)
    for index, row in enumerate(rows, 1):
        if provider == "ollama":
            print(
                f"Classifying {index}/{total}: {row['tweet_id']}",
                file=sys.stderr,
                flush=True,
            )
        result = classifier.classify(f"{row.get('text') or ''} {row.get('author') or ''}")
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
) -> dict[str, float | int]:
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    definitions = load_category_config(categories_path)
    classifier = _build_classifier(
        provider, definitions, ollama_model, ollama_url, ollama_timeout
    )
    rows = store.iter_bookmarks(
        only_unclassified=False,
        category=only_category,
        limit=limit,
    )
    start = time.perf_counter()
    for index, row in enumerate(rows, 1):
        if provider == "ollama":
            print(
                f"Benchmarking {index}/{len(rows)}: {row['tweet_id']}",
                file=sys.stderr,
                flush=True,
            )
        classifier.classify(f"{row.get('text') or ''} {row.get('author') or ''}")
    elapsed = time.perf_counter() - start
    count = len(rows)
    return {
        "count": count,
        "total_seconds": elapsed,
        "avg_seconds": elapsed / count if count else 0.0,
    }


def _write_initial_categories(
    categories_path: Path, interests: str | None, force: bool
) -> None:
    selected = _parse_csv(interests)
    if not selected:
        selected = ["virtualization", "kubernetes", "homelab", "ai", "security"]
    preset_rules = rules_for_interests(selected)

    if categories_path.exists() and not force:
        existing = load_category_rules(categories_path)
        rules = merge_category_rules(existing, preset_rules)
    else:
        rules = preset_rules
    if "General" not in rules:
        rules["General"] = []
    save_category_rules(rules, categories_path)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _build_classifier(
    provider: str,
    definitions: dict[str, CategoryDefinition],
    ollama_model: str,
    ollama_url: str,
    ollama_timeout: int,
) -> RuleBasedClassifier | OllamaClassifier:
    rules = {
        category: definition.keywords
        for category, definition in definitions.items()
    }
    if provider == "rules":
        return RuleBasedClassifier(rules)
    if provider == "ollama":
        return OllamaClassifier(
            categories=list(definitions.keys()),
            category_descriptions={
                category: definition.description
                for category, definition in definitions.items()
            },
            model=ollama_model,
            base_url=ollama_url,
            timeout_seconds=ollama_timeout,
        )
    raise ValueError(f"Unsupported provider: {provider}")


if __name__ == "__main__":
    raise SystemExit(main())
