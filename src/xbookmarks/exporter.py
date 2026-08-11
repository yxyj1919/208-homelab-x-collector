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
    raw = _raw_json(row)
    author = _author_profile(raw)
    author_label = _author_label(row, author)
    tag_html = "".join(f"<span>{html.escape(tag)}</span>" for tag in tags)
    media_html = _render_media(raw)
    card_html = _render_card(raw)
    quote_html = _render_quote(raw)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(row.get("author") or row["tweet_id"])}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; max-width: 860px; margin: 40px auto; padding: 0 20px; color: #1f2937; }}
    header {{ border-bottom: 1px solid #d1d5db; margin-bottom: 24px; padding-bottom: 16px; }}
    .meta {{ color: #6b7280; font-size: 14px; }}
    .text {{ white-space: pre-wrap; font-size: 18px; }}
    .tags span {{ display: inline-block; border: 1px solid #9ca3af; border-radius: 4px; padding: 2px 7px; margin-right: 6px; font-size: 13px; }}
    .media {{ display: grid; gap: 10px; margin: 20px 0; }}
    .media img {{ max-width: 100%; border: 1px solid #d1d5db; border-radius: 8px; }}
    .card, .quote {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 12px; margin: 18px 0; background: #f9fafb; }}
    .card h2, .quote h2 {{ font-size: 16px; margin: 0 0 6px; }}
    .quote .text {{ font-size: 15px; margin: 6px 0; }}
    a {{ color: #0f766e; }}
  </style>
</head>
<body>
  <header>
    <div class="meta">Category: {html.escape(row.get("category") or "General")}</div>
    <h1>{html.escape(author_label)}</h1>
    <div class="meta">{html.escape(row.get("created_at") or "Unknown date")} · <a href="{html.escape(row["url"])}">Original</a></div>
  </header>
  <main>
    <p class="text">{html.escape(row["text"])}</p>
    {media_html}
    {card_html}
    {quote_html}
    <p class="tags">{tag_html}</p>
    <p class="meta">{html.escape(row.get("reason") or "")}</p>
  </main>
</body>
</html>
"""


def _raw_json(row: dict) -> dict:
    value = row.get("raw_json")
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _author_profile(raw: dict) -> dict | None:
    author = raw.get("author")
    return author if isinstance(author, dict) else None


def _author_label(row: dict, author: dict | None) -> str:
    if author:
        screen_name = _text(author.get("screen_name") or author.get("username"))
        name = _text(author.get("name"))
        user_id = _text(author.get("user_id") or author.get("id"))
        if screen_name and name:
            return f"{name} (@{screen_name})"
        if screen_name:
            return f"@{screen_name}"
        if name:
            return name
        if user_id:
            return user_id
    return row.get("author") or "Unknown Author"


def _render_media(raw: dict) -> str:
    media = raw.get("media")
    if not isinstance(media, list):
        return ""
    items = []
    for item in media:
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url") or item.get("media_url_https"))
        if not url:
            continue
        media_type = _text(item.get("type")) or "media"
        alt = _text(item.get("alt_text")) or media_type
        if media_type == "photo":
            items.append(
                f'<img src="{html.escape(url)}" alt="{html.escape(alt)}" loading="lazy">'
            )
        else:
            items.append(f'<div><a href="{html.escape(url)}">{html.escape(media_type)}</a></div>')
    if not items:
        return ""
    return f"<section class=\"media\">{''.join(items)}</section>"


def _render_card(raw: dict) -> str:
    card = raw.get("card")
    if not isinstance(card, dict):
        return ""
    title = _text(card.get("title"))
    description = _text(card.get("description"))
    url = _text(card.get("url"))
    if not any((title, description, url)):
        return ""
    title_html = html.escape(title or "Linked card")
    if url:
        title_html = f'<a href="{html.escape(url)}">{title_html}</a>'
    desc_html = f"<p>{html.escape(description)}</p>" if description else ""
    return f'<section class="card"><h2>{title_html}</h2>{desc_html}</section>'


def _render_quote(raw: dict) -> str:
    quoted = raw.get("quoted_tweet")
    if not isinstance(quoted, dict):
        return ""
    tweet_id = _text(quoted.get("tweet_id") or quoted.get("id"))
    text = _text(quoted.get("full_text") or quoted.get("text"))
    author = quoted.get("author") if isinstance(quoted.get("author"), dict) else {}
    author_name = (
        _text(author.get("screen_name") or author.get("username"))
        or _text(author.get("name"))
        or "Quoted tweet"
    )
    title = f"Quoted: {author_name}"
    link = f"https://x.com/i/status/{tweet_id}" if tweet_id else ""
    title_html = html.escape(title)
    if link:
        title_html = f'<a href="{html.escape(link)}">{title_html}</a>'
    text_html = f'<p class="text">{html.escape(text)}</p>' if text else ""
    return f'<section class="quote"><h2>{title_html}</h2>{text_html}</section>'


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
