from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Bookmark


def load_bookmarks(path: Path) -> list[Bookmark]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = _extract_records(data)
    bookmarks: list[Bookmark] = []
    for record in records:
        bookmark = _record_to_bookmark(record)
        if bookmark is not None:
            bookmarks.append(bookmark)
    return bookmarks


def load_xarchive_bookmarks(path: Path) -> list[Bookmark]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("xarchive JSON input must be an object")
    records = data.get("bookmarks")
    if not isinstance(records, list):
        raise ValueError("xarchive JSON input must contain a bookmarks array")
    bookmarks: list[Bookmark] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        bookmark = _xarchive_record_to_bookmark(record)
        if bookmark is not None:
            bookmarks.append(bookmark)
    return bookmarks


def _extract_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("bookmarks", "tweets", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError("JSON input must be a list or an object containing bookmarks/tweets/data")


def _record_to_bookmark(record: dict[str, Any]) -> Bookmark | None:
    tweet_id = _first_text(record, "id", "tweet_id", "rest_id")
    text = _first_text(record, "text", "full_text", "content")
    url = _first_text(record, "url", "tweet_url", "expanded_url")
    author = _author_text(record)
    created_at = _first_text(record, "created_at", "created", "date")

    legacy = record.get("tweet")
    if isinstance(legacy, dict):
        tweet_id = tweet_id or _first_text(legacy, "id", "id_str", "rest_id")
        text = text or _first_text(legacy, "text", "full_text")
        author = author or _author_text(legacy)
        created_at = created_at or _first_text(legacy, "created_at")

    if not tweet_id or not text:
        return None

    if not url:
        url = f"https://x.com/i/status/{tweet_id}"

    return Bookmark(
        tweet_id=tweet_id,
        url=url,
        text=text,
        author=author,
        created_at=created_at,
        raw=record,
    )


def _xarchive_record_to_bookmark(record: dict[str, Any]) -> Bookmark | None:
    if record.get("status") == "unavailable":
        return None
    tweet_id = _first_text(record, "tweet_id", "id", "rest_id")
    text = _first_text(record, "full_text", "text", "content")
    if not tweet_id or not text:
        return None

    return Bookmark(
        tweet_id=tweet_id,
        url=_first_text(record, "url", "tweet_url") or f"https://x.com/i/status/{tweet_id}",
        text=text,
        author=_author_text(record),
        created_at=_first_text(record, "created_at", "created", "date"),
        raw=record,
    )


def _first_text(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _author_text(record: dict[str, Any]) -> str | None:
    author = record.get("author") or record.get("user")
    if isinstance(author, dict):
        return _first_text(author, "screen_name", "username", "name", "user_id", "id")
    return _first_text(record, "author", "screen_name", "username")
