from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .classifier import RuleBasedClassifier
from .config import DEFAULT_CATEGORIES, load_category_rules
from .exporter import export_html
from .models import Bookmark, ClassificationResult
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

    def import_extension_bookmarks(self, payload: dict[str, Any]) -> dict[str, Any]:
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        if len(items) > 5000:
            raise ValueError("items must contain at most 5000 bookmarks")

        bookmarks: list[Bookmark] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            bookmark = extension_bookmark(item)
            if bookmark is not None:
                bookmarks.append(bookmark)

        should_classify = bool(payload.get("classify", True))
        should_export = bool(payload.get("export_html", True))
        archive_dir = _archive_dir(payload.get("archive_dir"))
        run_id = self.store.begin_run(
            "extension-import",
            archive_dir=archive_dir if should_export else None,
            connector="chrome-extension",
            provider="rules" if should_classify else None,
            model="rules" if should_classify else None,
        )
        classified = 0
        exported = 0
        try:
            result = self.store.upsert_bookmarks(bookmarks)
            if should_classify:
                classified = self._classify_unclassified_with_rules()
            if should_export:
                exported = export_html(self.store, archive_dir)
            classified_total = _summary_int(payload, "classified", 0) + classified
            self.store.set_sync_state("last_connector", "chrome-extension")
            self.store.set_sync_state(
                "chrome-extension.source_url",
                _text(payload.get("source_url")) or "",
            )
            self.store.set_sync_state(
                "chrome-extension.result_count",
                str(result.unique_seen),
            )
            self.store.finish_run(
                run_id,
                "succeeded",
                imported_count=_summary_int(payload, "imported", result.imported),
                classified_count=classified_total,
                exported_count=exported,
                inserted_count=_summary_int(payload, "inserted", result.inserted),
                updated_count=_summary_int(payload, "updated", result.updated),
                unchanged_count=_summary_int(payload, "unchanged", result.unchanged),
                duplicate_count=_summary_int(payload, "duplicates", result.duplicates),
                source_count=_summary_int(payload, "source", result.total_seen),
            )
        except Exception as exc:
            self.store.finish_run(run_id, "failed", message=str(exc))
            raise

        return {
            "run_id": run_id,
            "total_seen": _summary_int(payload, "source", result.total_seen),
            "unique_seen": _summary_int(payload, "unique", result.unique_seen),
            "imported": _summary_int(payload, "imported", result.imported),
            "inserted": _summary_int(payload, "inserted", result.inserted),
            "updated": _summary_int(payload, "updated", result.updated),
            "unchanged": _summary_int(payload, "unchanged", result.unchanged),
            "duplicates": _summary_int(payload, "duplicates", result.duplicates),
            "classified": classified_total,
            "exported": exported,
            "archive_dir": str(archive_dir) if should_export else None,
        }

    def _classify_unclassified_with_rules(self) -> int:
        classifier = RuleBasedClassifier(load_category_rules(DEFAULT_CATEGORIES))
        rows = self.store.iter_bookmarks(only_unclassified=True, skip_manual=True)
        for row in rows:
            result = classifier.classify(f"{row.get('text') or ''} {row.get('author') or ''}")
            self.store.save_classification(row["tweet_id"], result, provider="rules")
        return len(rows)

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
            provider="manual",
        )


def bookmark_updates(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "category",
        "tags",
        "notes",
        "read_state",
        "important",
        "archived",
        "review_state",
    }
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
    if "review_state" in payload:
        value = payload["review_state"]
        if value not in {"pending", "accepted"}:
            raise ValueError("review_state must be pending or accepted")
        updates["review_state"] = value
    return updates


def extension_bookmark(record: dict[str, Any]) -> Bookmark | None:
    tweet_id = _text(record.get("tweet_id") or record.get("id"))
    text = _text(record.get("text") or record.get("full_text"))
    if not tweet_id or not text:
        return None

    url = _text(record.get("url")) or f"https://x.com/i/status/{tweet_id}"
    author_value = record.get("author")
    author = _extension_author_text(author_value)
    created_at = _text(record.get("created_at"))
    return Bookmark(
        tweet_id=tweet_id,
        url=url,
        text=text,
        author=author,
        created_at=created_at,
        raw={
            **record,
            "source": "chrome-extension",
        },
    )


def _extension_author_text(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(
            value.get("screen_name")
            or value.get("username")
            or value.get("name")
            or value.get("user_id")
            or value.get("id")
        )
    return _text(value)


def _archive_dir(value: Any) -> Path:
    text = _text(value) or "archive"
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("archive_dir must be a relative path inside the project")
    return path


def _summary_int(payload: dict[str, Any], name: str, default: int) -> int:
    summary = payload.get("summary")
    if not isinstance(summary, dict) or name not in summary:
        return default
    try:
        value = int(summary[name])
    except (TypeError, ValueError):
        raise ValueError(f"summary.{name} must be an integer")
    if value < 0:
        raise ValueError(f"summary.{name} must not be negative")
    return value


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
        "provider": row.get("classification_provider"),
        "reason": row.get("reason"),
        "notes": row.get("notes") or "",
        "read_state": row.get("read_state") or "unread",
        "important": bool(row.get("important")),
        "archived": bool(row.get("archived")),
        "review_state": row.get("review_state") or "pending",
        "review_reason": row.get("review_reason"),
        "reviewed_at": row.get("reviewed_at"),
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
