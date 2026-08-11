from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ClassificationResult
from .storage import BookmarkStore


class BookmarkService:
    def __init__(self, store: BookmarkStore) -> None:
        self.store = store

    @classmethod
    def from_db_path(cls, db_path: Path) -> "BookmarkService":
        store = BookmarkStore(db_path)
        store.init()
        return cls(store)

    def list_bookmarks(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, list[dict[str, Any]]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0 or offset > 100000:
            raise ValueError("offset must be between 0 and 100000")
        rows = self.store.list_bookmarks(
            query=_blank_to_none(query),
            category=_blank_to_none(category),
            status=_blank_to_none(status),
            limit=limit,
            offset=offset,
        )
        return {"items": [bookmark_payload(row) for row in rows]}

    def category_summary(self) -> dict[str, Any]:
        return {
            "categories": [
                {"name": category, "count": count}
                for category, count in self.store.category_counts()
            ],
            "statuses": self.store.status_counts(),
        }

    def sync_status(self, *, latest_limit: int = 5) -> dict[str, Any]:
        if latest_limit < 1:
            raise ValueError("latest_limit must be at least 1")
        latest_runs = self.store.list_run_logs(limit=latest_limit)
        return {
            "state": self.store.sync_state(),
            "latest_runs": latest_runs,
            "summary": sync_summary(latest_runs[0] if latest_runs else None),
        }

    def update_bookmark(
        self, tweet_id: str, payload: dict[str, Any]
    ) -> dict[str, dict[str, Any] | None]:
        updates = bookmark_updates(payload)
        self.store.update_bookmark(tweet_id, **updates)
        row = self.store.get_bookmark(tweet_id)
        return {"item": bookmark_payload(row) if row else None}

    def set_bookmark_category(
        self,
        tweet_id: str,
        category: str,
        *,
        tags: list[str] | None = None,
        reason: str = "Manually adjusted by user.",
    ) -> None:
        self.store.save_classification(
            tweet_id,
            ClassificationResult(
                category=category,
                tags=tags or [],
                confidence=1.0,
                reason=reason,
            ),
            source="manual",
        )


def bookmark_updates(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"category", "tags", "notes", "read_state", "important", "archived"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Unsupported field(s): {', '.join(unknown)}")

    updates: dict[str, Any] = {}
    for key in ("category", "notes", "read_state"):
        if key in payload:
            value = payload[key]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            updates[key] = value or ""
    if "tags" in payload:
        tags = payload["tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("tags must be a list of strings")
        updates["tags"] = [tag.strip() for tag in tags if tag.strip()]
    for key in ("important", "archived"):
        if key in payload:
            if not isinstance(payload[key], bool):
                raise ValueError(f"{key} must be a boolean")
            updates[key] = payload[key]
    return updates


def bookmark_payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = _raw_json(row)
    return {
        "tweet_id": row["tweet_id"],
        "url": row["url"],
        "text": row["text"],
        "author": row.get("author"),
        "created_at": row.get("created_at"),
        "category": row.get("category") or "Unclassified",
        "category_source": row.get("category_source"),
        "tags": _json_list(row.get("tags_json")),
        "confidence": row.get("confidence"),
        "reason": row.get("reason"),
        "notes": row.get("notes") or "",
        "read_state": row.get("read_state") or "unread",
        "important": bool(row.get("important")),
        "archived": bool(row.get("archived")),
        "export_path": row.get("export_path"),
        "updated_at": row.get("updated_at"),
        "author_profile": _author_profile(raw),
        "media": _media_payload(raw),
        "card": _card_payload(raw),
        "quoted_tweet": _quoted_tweet_payload(raw),
    }


def sync_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "connector": row.get("connector"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "finished_at": row.get("ended_at") or row.get("started_at"),
        "imported": int(row.get("imported_count") or 0),
        "inserted": int(row.get("inserted_count") or 0),
        "updated": int(row.get("updated_count") or 0),
        "unchanged": int(row.get("unchanged_count") or 0),
        "duplicates": int(row.get("duplicate_count") or 0),
        "classified": int(row.get("classified_count") or 0),
        "exported": int(row.get("exported_count") or 0),
        "pages_fetched": int(row.get("pages_fetched") or 0),
        "source_count": int(row.get("source_count") or 0),
        "has_more": bool(row.get("has_more")),
        "cursor_before": row.get("cursor_before"),
        "cursor_after": row.get("cursor_after"),
        "message": row.get("message"),
    }


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _raw_json(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("raw_json")
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _author_profile(raw: dict[str, Any]) -> dict[str, Any] | None:
    author = raw.get("author")
    if not isinstance(author, dict):
        return None
    return {
        "user_id": _text(author.get("user_id") or author.get("id")),
        "screen_name": _text(author.get("screen_name") or author.get("username")),
        "name": _text(author.get("name")),
        "profile_image_url": _text(author.get("profile_image_url")),
        "verified": bool(author.get("verified")),
        "followers_count": int(author.get("followers_count") or 0),
    }


def _media_payload(raw: dict[str, Any]) -> list[dict[str, str | None]]:
    media = raw.get("media")
    if not isinstance(media, list):
        return []
    payload = []
    for item in media:
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url") or item.get("media_url_https"))
        if not url:
            continue
        payload.append(
            {
                "type": _text(item.get("type")) or "media",
                "url": url,
                "alt_text": _text(item.get("alt_text")),
            }
        )
    return payload


def _card_payload(raw: dict[str, Any]) -> dict[str, str | None] | None:
    card = raw.get("card")
    if not isinstance(card, dict):
        return None
    url = _text(card.get("url"))
    title = _text(card.get("title"))
    description = _text(card.get("description"))
    if not any((url, title, description)):
        return None
    return {
        "type": _text(card.get("type")),
        "url": url,
        "title": title,
        "description": description,
    }


def _quoted_tweet_payload(raw: dict[str, Any]) -> dict[str, Any] | None:
    quoted = raw.get("quoted_tweet")
    if not isinstance(quoted, dict):
        return None
    tweet_id = _text(quoted.get("tweet_id") or quoted.get("id"))
    text = _text(quoted.get("full_text") or quoted.get("text"))
    if not any((tweet_id, text)):
        return None
    author = quoted.get("author") if isinstance(quoted.get("author"), dict) else {}
    return {
        "tweet_id": tweet_id,
        "text": text,
        "author": {
            "screen_name": _text(author.get("screen_name") or author.get("username")),
            "name": _text(author.get("name")),
        },
        "url": f"https://x.com/i/status/{tweet_id}" if tweet_id else None,
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
