from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .importer import load_bookmarks, load_xarchive_bookmarks
from .models import Bookmark
from .secrets import DEFAULT_SECRET_PATH, SecretStore


CONNECTOR_NAMES = ("json-file", "xarchive-json", "x-api")
DEFAULT_X_API_BASE_URL = "https://api.x.com"
DEFAULT_X_API_PAGE_SIZE = 100
DEFAULT_X_API_MAX_PAGES = 10


@dataclass(frozen=True)
class SyncBatch:
    bookmarks: list[Bookmark]
    next_cursor: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class BookmarkConnector(Protocol):
    name: str

    def sync(self, cursor: str | None = None) -> SyncBatch:
        """Fetch a batch of bookmarks from the connector source."""


@dataclass(frozen=True)
class ConnectorOptions:
    name: str
    input_path: Path | None = None
    x_user_id: str | None = None
    x_bearer_token_env: str = "X_BEARER_TOKEN"
    x_token_file: Path | None = None
    x_secret_path: Path = DEFAULT_SECRET_PATH
    x_api_base_url: str = DEFAULT_X_API_BASE_URL
    x_page_size: int = DEFAULT_X_API_PAGE_SIZE
    x_max_pages: int = DEFAULT_X_API_MAX_PAGES


class ConnectorError(RuntimeError):
    pass


class ConnectorAuthError(ConnectorError):
    pass


class ConnectorRateLimitError(ConnectorError):
    pass


class ConnectorResponseError(ConnectorError):
    pass


class ConnectorCapabilityError(ConnectorError):
    pass


class JsonFileConnector:
    name = "json-file"

    def __init__(self, input_path: Path) -> None:
        self.input_path = input_path

    def sync(self, cursor: str | None = None) -> SyncBatch:
        del cursor
        bookmarks = load_bookmarks(self.input_path)
        stat = self.input_path.stat()
        return SyncBatch(
            bookmarks=bookmarks,
            next_cursor=f"{stat.st_mtime_ns}:{stat.st_size}",
            metadata={"source_path": str(self.input_path)},
        )


class XArchiveJsonConnector:
    name = "xarchive-json"

    def __init__(self, input_path: Path) -> None:
        self.input_path = input_path

    def sync(self, cursor: str | None = None) -> SyncBatch:
        del cursor
        bookmarks = load_xarchive_bookmarks(self.input_path)
        stat = self.input_path.stat()
        return SyncBatch(
            bookmarks=bookmarks,
            next_cursor=f"{stat.st_mtime_ns}:{stat.st_size}",
            metadata={
                "source_path": str(self.input_path),
                "result_count": str(len(bookmarks)),
            },
        )


class XApiConnector:
    name = "x-api"

    def __init__(
        self,
        user_id: str,
        credential_manager: XApiCredentialManager,
        base_url: str = DEFAULT_X_API_BASE_URL,
        page_size: int = DEFAULT_X_API_PAGE_SIZE,
        max_pages: int = DEFAULT_X_API_MAX_PAGES,
        client: XApiClient | None = None,
    ) -> None:
        if page_size < 1 or page_size > 100:
            raise ValueError("x-api page size must be between 1 and 100")
        if max_pages < 1:
            raise ValueError("x-api max pages must be at least 1")
        self.user_id = user_id
        self.page_size = page_size
        self.max_pages = max_pages
        self.credential_manager = credential_manager
        self.client = client or XApiClient(base_url=base_url)

    def sync(self, cursor: str | None = None) -> SyncBatch:
        bookmarks: list[Bookmark] = []
        pagination_cursor, high_watermark = _parse_x_api_cursor(cursor)
        next_token = pagination_cursor
        first_seen_id: str | None = None
        pages_fetched = 0
        result_count = 0
        reached_cursor = False
        for _ in range(self.max_pages):
            payload = self._get_bookmarks_with_refresh(
                max_results=self.page_size, pagination_token=next_token
            )
            pages_fetched += 1
            _raise_for_x_errors(payload)
            for record in _x_records(payload):
                bookmark = _x_record_to_bookmark(record, payload)
                if bookmark is not None:
                    if first_seen_id is None:
                        first_seen_id = bookmark.tweet_id
                    if high_watermark and bookmark.tweet_id == high_watermark:
                        reached_cursor = True
                        break
                    bookmarks.append(bookmark)
            meta = payload.get("meta")
            if isinstance(meta, dict):
                result_count += _int_or_zero(meta.get("result_count"))
                next_token = _clean_text(meta.get("next_token"))
            else:
                next_token = None
            if reached_cursor:
                next_token = None
                break
            if not next_token:
                break
        if next_token:
            next_cursor = f"page:{next_token}"
            has_more = "true"
        elif first_seen_id:
            next_cursor = f"tweet:{first_seen_id}"
            has_more = "false"
        else:
            next_cursor = cursor
            has_more = "false"
        metadata = {
            "pages_fetched": str(pages_fetched),
            "result_count": str(result_count),
            "reached_cursor": "true" if reached_cursor else "false",
        }
        metadata["has_more"] = has_more
        return SyncBatch(bookmarks=bookmarks, next_cursor=next_cursor, metadata=metadata)

    def capability_check(self) -> SyncBatch:
        payload = self._get_bookmarks_with_refresh(max_results=1, pagination_token=None)
        _raise_for_x_errors(payload)
        meta = payload.get("meta")
        result_count = _int_or_zero(meta.get("result_count")) if isinstance(meta, dict) else 0
        return SyncBatch(
            bookmarks=[],
            metadata={
                "status": "ok",
                "endpoint": "/2/users/{id}/bookmarks",
                "result_count": str(result_count),
            },
        )

    def _get_bookmarks_with_refresh(
        self, max_results: int, pagination_token: str | None
    ) -> dict[str, Any]:
        token = self.credential_manager.access_token()
        try:
            return self.client.get_bookmarks(
                bearer_token=token,
                user_id=self.user_id,
                max_results=max_results,
                pagination_token=pagination_token,
            )
        except ConnectorAuthError:
            refreshed_token = self.credential_manager.refresh_access_token(self.client)
            return self.client.get_bookmarks(
                bearer_token=refreshed_token,
                user_id=self.user_id,
                max_results=max_results,
                pagination_token=pagination_token,
            )


class XApiClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 30,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urllib.request.build_opener()

    def get_bookmarks(
        self,
        bearer_token: str,
        user_id: str,
        max_results: int,
        pagination_token: str | None = None,
    ) -> dict[str, Any]:
        path_user_id = urllib.parse.quote(user_id, safe="")
        params = {
            "max_results": str(max_results),
            "tweet.fields": "created_at,author_id",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        url = (
            f"{self.base_url}/2/users/{path_user_id}/bookmarks?"
            + urllib.parse.urlencode(params)
        )
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            _raise_for_http_error(exc.code, body)
        except urllib.error.URLError as exc:
            raise ConnectorResponseError(f"x-api request failed: {exc.reason}") from exc
        return _loads_json(raw)

    def refresh_access_token(
        self, client_id: str, refresh_token: str
    ) -> dict[str, Any]:
        url = f"{self.base_url}/2/oauth2/token"
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            _raise_for_http_error(exc.code, body_text)
        except urllib.error.URLError as exc:
            raise ConnectorResponseError(
                f"x-api token refresh failed: {exc.reason}"
            ) from exc
        return _loads_json(raw)


class XApiCredentialManager:
    def __init__(
        self,
        secret_store: SecretStore,
        env_name: str = "X_BEARER_TOKEN",
        token_file: Path | None = None,
    ) -> None:
        self.secret_store = secret_store
        self.env_name = env_name
        self.token_file = token_file

    def access_token(self) -> str:
        secrets = self.secret_store.load_oauth()
        if secrets.access_token:
            return secrets.access_token
        return read_bearer_token(env_name=self.env_name, token_file=self.token_file)

    def refresh_access_token(self, client: XApiClient) -> str:
        secrets = self.secret_store.load_oauth()
        if not secrets.has_refresh:
            raise ConnectorAuthError(
                "X OAuth refresh requires client_id and refresh_token in the secret store."
            )
        payload = client.refresh_access_token(
            client_id=str(secrets.client_id),
            refresh_token=str(secrets.refresh_token),
        )
        access_token = _clean_text(payload.get("access_token"))
        if not access_token:
            raise ConnectorAuthError("X OAuth refresh response did not include access_token")
        refresh_token = _clean_text(payload.get("refresh_token")) or secrets.refresh_token
        self.secret_store.save_oauth(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=secrets.client_id,
        )
        return access_token


def build_connector(options: ConnectorOptions) -> BookmarkConnector:
    if options.name == "json-file":
        if options.input_path is None:
            raise ValueError("json-file connector requires --input")
        return JsonFileConnector(options.input_path)
    if options.name == "xarchive-json":
        if options.input_path is None:
            raise ValueError("xarchive-json connector requires --input")
        return XArchiveJsonConnector(options.input_path)
    if options.name == "x-api":
        if not options.x_user_id or not options.x_user_id.strip():
            raise ValueError("x-api connector requires --x-user-id")
        credential_manager = XApiCredentialManager(
            secret_store=SecretStore(options.x_secret_path),
            env_name=options.x_bearer_token_env,
            token_file=options.x_token_file,
        )
        return XApiConnector(
            user_id=options.x_user_id.strip(),
            credential_manager=credential_manager,
            base_url=options.x_api_base_url,
            page_size=options.x_page_size,
            max_pages=options.x_max_pages,
        )
    raise ValueError(f"Unsupported connector: {options.name}")


def read_bearer_token(env_name: str, token_file: Path | None = None) -> str:
    if token_file is not None:
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
        raise ConnectorAuthError(f"Token file is empty: {token_file}")
    token = os.environ.get(env_name, "").strip()
    if token:
        return token
    raise ConnectorAuthError(
        f"Missing X bearer token. Set {env_name} or pass --x-token-file."
    )


def _x_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _parse_x_api_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    value = _clean_text(cursor)
    if not value:
        return None, None
    if value.startswith("page:"):
        token = value[len("page:") :].strip()
        return token or None, None
    if value.startswith("tweet:"):
        tweet_id = value[len("tweet:") :].strip()
        return None, tweet_id or None
    return value, None


def _x_record_to_bookmark(
    record: dict[str, Any], payload: dict[str, Any]
) -> Bookmark | None:
    tweet_id = _clean_text(record.get("id"))
    text = _clean_text(record.get("text"))
    if not tweet_id or not text:
        return None
    author_id = _clean_text(record.get("author_id"))
    author = _author_from_includes(payload, author_id) or author_id
    return Bookmark(
        tweet_id=tweet_id,
        url=f"https://x.com/i/status/{tweet_id}",
        text=text,
        author=author,
        created_at=_clean_text(record.get("created_at")),
        raw={**record, "source": "x-api"},
    )


def _author_from_includes(payload: dict[str, Any], author_id: str | None) -> str | None:
    includes = payload.get("includes")
    if not isinstance(includes, dict):
        return None
    users = includes.get("users")
    if not isinstance(users, list):
        return None
    for user in users:
        if not isinstance(user, dict):
            continue
        if author_id and _clean_text(user.get("id")) != author_id:
            continue
        return _clean_text(user.get("username")) or _clean_text(user.get("name"))
    return None


def _raise_for_x_errors(payload: dict[str, Any]) -> None:
    errors = payload.get("errors")
    if not errors:
        return
    if not isinstance(errors, list):
        raise ConnectorResponseError("x-api response errors field is not a list")
    messages = []
    statuses = set()
    for error in errors:
        if not isinstance(error, dict):
            continue
        status = _int_or_zero(error.get("status"))
        if status:
            statuses.add(status)
        title = _clean_text(error.get("title")) or "X API error"
        detail = _clean_text(error.get("detail"))
        messages.append(f"{title}: {detail}" if detail else title)
    message = "; ".join(messages) if messages else "x-api returned errors"
    if statuses & {401, 403}:
        raise ConnectorAuthError(message)
    if 429 in statuses:
        raise ConnectorRateLimitError(message)
    raise ConnectorResponseError(message)


def _raise_for_http_error(status: int, body: str) -> None:
    message = _http_error_message(status, body)
    if status in {401, 403}:
        raise ConnectorAuthError(message)
    if status == 429:
        raise ConnectorRateLimitError(message)
    raise ConnectorResponseError(message)


def _http_error_message(status: int, body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            parts = []
            for error in errors:
                if isinstance(error, dict):
                    title = _clean_text(error.get("title")) or "X API error"
                    detail = _clean_text(error.get("detail"))
                    parts.append(f"{title}: {detail}" if detail else title)
            if parts:
                return f"x-api HTTP {status}: " + "; ".join(parts)
        title = _clean_text(payload.get("title"))
        detail = _clean_text(payload.get("detail"))
        if title or detail:
            return f"x-api HTTP {status}: {title or detail}"
    return f"x-api HTTP {status}"


def _loads_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectorResponseError("x-api returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ConnectorResponseError("x-api response must be a JSON object")
    return payload


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_zero(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
