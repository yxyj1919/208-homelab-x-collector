from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .storage import BookmarkStore


def export_html(store: BookmarkStore, archive_dir: Path) -> int:
    archive_dir.mkdir(parents=True, exist_ok=True)
    rows = store.iter_bookmarks()
    count = 0
    for row in rows:
        category = row.get("category") or "General"
        category_dir = archive_dir / _safe_path_segment(category)
        category_dir.mkdir(parents=True, exist_ok=True)
        file_path = category_dir / _bookmark_filename(row)
        file_path.write_text(_render_bookmark(row), encoding="utf-8")
        _remove_previous_export(row.get("export_path"), file_path, archive_dir)
        store.save_export_path(row["tweet_id"], file_path)
        count += 1

    index_dir = archive_dir / "_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.html").write_text(_render_index(rows), encoding="utf-8")
    return count


def _bookmark_filename(row: dict) -> str:
    date = (row.get("created_at") or "unknown")[:10]
    date = re.sub(r"[^0-9A-Za-z_-]+", "-", date).strip("-") or "unknown"
    return f"{date}_{_safe_path_segment(row['tweet_id'])}.html"


def _safe_path_segment(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", value.strip())
    return cleaned.strip("-") or "untitled"


def _remove_previous_export(
    previous_path: str | None, current_path: Path, archive_dir: Path
) -> None:
    if not previous_path:
        return

    previous = Path(previous_path)
    if previous == current_path or not previous.exists() or not previous.is_file():
        return

    try:
        previous.relative_to(archive_dir)
    except ValueError:
        return

    previous.unlink()


def _tags(row: dict) -> list[str]:
    try:
        parsed = json.loads(row.get("tags_json") or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed]


def _render_bookmark(row: dict) -> str:
    tags = _tags(row)
    tag_html = "".join(f"<span>{html.escape(tag)}</span>" for tag in tags)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(row.get("author") or row["tweet_id"])}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; max-width: 820px; margin: 40px auto; padding: 0 20px; color: #1f2937; }}
    header {{ border-bottom: 1px solid #d1d5db; margin-bottom: 24px; padding-bottom: 16px; }}
    .meta {{ color: #6b7280; font-size: 14px; }}
    .text {{ white-space: pre-wrap; font-size: 18px; }}
    .tags span {{ display: inline-block; border: 1px solid #9ca3af; border-radius: 4px; padding: 2px 7px; margin-right: 6px; font-size: 13px; }}
    a {{ color: #0f766e; }}
  </style>
</head>
<body>
  <header>
    <div class="meta">Category: {html.escape(row.get("category") or "General")}</div>
    <h1>{html.escape(row.get("author") or "Unknown Author")}</h1>
    <div class="meta">{html.escape(row.get("created_at") or "Unknown date")} · <a href="{html.escape(row["url"])}">Original</a></div>
  </header>
  <main>
    <p class="text">{html.escape(row["text"])}</p>
    <p class="tags">{tag_html}</p>
    <p class="meta">{html.escape(row.get("reason") or "")}</p>
  </main>
</body>
</html>
"""


def _render_index(rows: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("category") or "General", []).append(row)

    sections = []
    for category in sorted(groups):
        items = []
        for row in groups[category]:
            file_name = _bookmark_filename(row)
            href = f"../{_safe_path_segment(category)}/{file_name}"
            title = row.get("text", "").strip().replace("\n", " ")
            if len(title) > 120:
                title = title[:117] + "..."
            items.append(
                f'<li><a href="{html.escape(href)}">{html.escape(title)}</a>'
                f'<div>{html.escape(row.get("author") or "Unknown")} · '
                f'{html.escape(row.get("created_at") or "Unknown date")}</div></li>'
            )
        sections.append(
            f"<section><h2>{html.escape(category)} ({len(groups[category])})</h2>"
            f"<ul>{''.join(items)}</ul></section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>X Bookmarks Archive</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.5; max-width: 980px; margin: 32px auto; padding: 0 20px; color: #1f2937; }}
    h1 {{ margin-bottom: 8px; }}
    section {{ border-top: 1px solid #d1d5db; padding-top: 16px; margin-top: 24px; }}
    li {{ margin: 12px 0; }}
    li div {{ color: #6b7280; font-size: 14px; margin-top: 2px; }}
    a {{ color: #0f766e; }}
  </style>
</head>
<body>
  <h1>X Bookmarks Archive</h1>
  <p>{len(rows)} bookmarks exported.</p>
  {''.join(sections)}
</body>
</html>
"""
