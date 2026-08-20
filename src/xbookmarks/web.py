from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .services import BookmarkService


def run_web_server(db_path: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    service = BookmarkService.from_db_path(db_path)

    class Handler(XBookmarksHandler):
        bookmark_service = service

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving X Bookmarks UI at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped X Bookmarks UI.")


class XBookmarksHandler(BaseHTTPRequestHandler):
    bookmark_service: BookmarkService

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path == "/api/bookmarks":
            self._handle_list_bookmarks(parsed.query)
            return
        if parsed.path == "/api/categories":
            self._handle_categories()
            return
        if parsed.path == "/api/review-summary":
            self._handle_review_summary()
            return
        if parsed.path == "/api/sync-status":
            self._handle_sync_status()
            return
        if parsed.path == "/api/settings":
            self._handle_settings()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        prefix = "/api/bookmarks/"
        if parsed.path.startswith(prefix):
            tweet_id = unquote(parsed.path[len(prefix) :])
            self._handle_update_bookmark(tweet_id)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/settings":
            self._handle_update_settings()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/bookmarks/bulk":
            self._handle_bulk_bookmarks()
            return
        if parsed.path == "/api/classify":
            self._handle_classify()
            return
        if parsed.path == "/api/settings/ollama-models":
            self._handle_ollama_models()
            return
        if parsed.path == "/api/extension/bookmarks":
            self._handle_extension_bookmarks()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_list_bookmarks(self, query_string: str) -> None:
        params = parse_qs(query_string)
        try:
            limit = _int_param(params, "limit", 50, minimum=1, maximum=200)
            offset = _int_param(params, "offset", 0, minimum=0, maximum=100000)
            payload = self.bookmark_service.list_bookmarks(
                query=_first(params, "query"),
                category=_first(params, "category"),
                status=_first(params, "status"),
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(payload)

    def _handle_categories(self) -> None:
        self._send_json(self.bookmark_service.category_summary())

    def _handle_review_summary(self) -> None:
        self._send_json(self.bookmark_service.review_summary())

    def _handle_sync_status(self) -> None:
        self._send_json(self.bookmark_service.sync_status(latest_limit=5))

    def _handle_settings(self) -> None:
        try:
            self._send_json(self.bookmark_service.settings())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _handle_update_settings(self) -> None:
        try:
            payload = self._read_json()
            response = self.bookmark_service.update_settings(payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(response)

    def _handle_update_bookmark(self, tweet_id: str) -> None:
        try:
            payload = self._read_json()
            response = self.bookmark_service.update_bookmark(tweet_id, payload)
        except KeyError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(response)

    def _handle_bulk_bookmarks(self) -> None:
        try:
            payload = self._read_json()
            response = self.bookmark_service.bulk_update_bookmarks(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(response)

    def _handle_classify(self) -> None:
        try:
            payload = self._read_json()
            response = self.bookmark_service.classify_bookmarks(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(response)

    def _handle_ollama_models(self) -> None:
        try:
            payload = self._read_json()
            response = self.bookmark_service.ollama_models(payload)
        except (RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(response)

    def _handle_extension_bookmarks(self) -> None:
        try:
            payload = self._read_json()
            response = self.bookmark_service.import_extension_bookmarks(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(response, status=HTTPStatus.CREATED)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")
        return payload

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)


def _first(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _int_param(
    params: dict[str, list[str]],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = _first(params, name)
    value = default if raw is None else int(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>X Bookmarks</title>
  <style>
    :root { color-scheme: light; --bg: #f4f6f8; --panel: #ffffff; --panel-soft: #f8fafc; --text: #182230; --muted: #667085; --line: #d6dce6; --accent: #0f766e; --accent-soft: #e6f4f1; --warning: #b54708; --warning-soft: #fff4e5; --danger: #b42318; --danger-soft: #fee4e2; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    header { padding: 16px 24px 14px; border-bottom: 1px solid var(--line); background: rgba(255, 255, 255, 0.96); position: sticky; top: 0; z-index: 5; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }
    h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0; }
    .header-top { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 12px; }
    .toolbar { display: grid; grid-template-columns: minmax(260px, 1fr) 180px 170px auto; gap: 10px; align-items: center; }
    .ai-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    input, select, textarea, button { font: inherit; }
    input, select, textarea { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; background: #fff; color: var(--text); }
    button { border: 1px solid var(--line); border-radius: 6px; padding: 9px 12px; background: #fff; color: var(--text); cursor: pointer; }
    button:hover { border-color: #98a2b3; }
    button:disabled { color: #98a2b3; cursor: not-allowed; background: #f2f4f7; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.danger { color: var(--danger); }
    .dashboard { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
    .metric { border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); padding: 9px 10px; min-width: 0; }
    .metric-label { color: var(--muted); font-size: 12px; line-height: 1.2; }
    .metric-value { margin-top: 4px; font-size: 13px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    main { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 18px; padding: 18px 24px; }
    .status { color: var(--muted); font-size: 13px; margin-top: 10px; min-height: 18px; }
    .list { display: grid; gap: 10px; align-content: start; }
    .item { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 13px; cursor: pointer; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }
    .item:hover { border-color: #b7c0cd; }
    .item[aria-selected="true"] { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset, 0 1px 2px rgba(16, 24, 40, 0.04); }
    .item-head { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: start; }
    .item-head input { width: auto; margin-top: 3px; }
    .item-title { min-width: 0; }
    .item-author { font-size: 14px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .item-date { color: var(--muted); font-size: 12px; line-height: 1.35; margin-top: 1px; }
    .item-open { color: var(--accent); font-size: 12px; text-decoration: none; white-space: nowrap; margin-top: 2px; }
    .meta, .tags, .sync { color: var(--muted); font-size: 13px; line-height: 1.4; }
    .text { white-space: pre-wrap; margin: 10px 0 8px; line-height: 1.5; }
    .note { color: #344054; background: #f2f4f7; border-left: 3px solid var(--accent); margin-top: 8px; padding: 7px 9px; font-size: 13px; line-height: 1.4; }
    .tag { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; margin: 4px 4px 0 0; background: #fff; color: #475467; }
    .badges { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
    .badge { border: 1px solid var(--line); border-radius: 999px; color: #475467; background: #fff; font-size: 12px; padding: 2px 7px; }
    .badge.category { background: var(--accent-soft); border-color: #99d5cc; color: #0f766e; }
    .badge.pending { background: var(--warning-soft); border-color: #f7c78b; color: var(--warning); }
    .badge.important { background: var(--danger-soft); border-color: #fda29b; color: var(--danger); }
    .badge.archived, .badge.read { background: #f2f4f7; }
    .item-media { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 150px)); gap: 8px; margin-top: 10px; }
    .item-media img { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
    .item-media a { display: inline-flex; align-items: center; min-height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; color: var(--accent); background: #fff; font-size: 13px; }
    .review-panel { border: 1px solid #f7c78b; border-radius: 8px; background: var(--warning-soft); padding: 10px; font-size: 13px; line-height: 1.45; }
    .review-panel strong { display: block; margin-bottom: 4px; }
    aside { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; align-self: start; position: sticky; top: 116px; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }
    aside h2 { margin: 0 0 12px; font-size: 16px; letter-spacing: 0; word-break: break-word; }
    .editor-section { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 12px; }
    .editor-section:first-child { border-top: 0; padding-top: 0; margin-top: 0; }
    .section-title { color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 8px; }
    .rich { display: grid; gap: 10px; }
    .author-box, .link-card, .quote-box { border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); padding: 10px; }
    .author-box strong, .link-card strong, .quote-box strong { display: block; margin-bottom: 4px; }
    .media-grid { display: grid; gap: 8px; }
    .media-grid img { width: 100%; max-height: 280px; object-fit: contain; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    .link-card p, .quote-box p { margin: 5px 0 0; color: #344054; font-size: 13px; line-height: 1.4; }
    label { display: block; color: var(--muted); font-size: 13px; margin: 12px 0 5px; }
    .checks { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
    .checks label { display: inline-flex; align-items: center; gap: 6px; margin: 0; color: var(--text); }
    .checks input { width: auto; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .bulk-actions { display: grid; grid-template-columns: auto auto auto minmax(160px, 220px) auto; gap: 8px; align-items: center; margin-bottom: 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 10px; }
    .bulk-actions[data-active="false"] { display: none; }
    .bulk-actions span { color: var(--muted); font-size: 13px; }
    .empty { color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; padding: 24px; text-align: center; }
    dialog { border: 1px solid var(--line); border-radius: 8px; padding: 0; width: min(420px, calc(100vw - 32px)); max-height: calc(100vh - 32px); color: var(--text); }
    dialog::backdrop { background: rgba(15, 23, 42, 0.28); }
    .modal { padding: 18px; background: var(--panel); max-height: calc(100vh - 32px); overflow: auto; }
    .modal h2 { margin: 0 0 12px; font-size: 18px; letter-spacing: 0; }
    .modal-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: -18px -18px 12px; padding: 14px 18px; background: var(--panel); border-bottom: 1px solid var(--line); position: sticky; top: -18px; z-index: 2; }
    .modal-header h2 { margin: 0; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
    .settings-modal { width: min(900px, calc(100vw - 32px)); }
    .settings-grid { display: grid; grid-template-columns: minmax(0, 1fr) 120px auto; gap: 10px; align-items: end; }
    .settings-model-row { margin-top: 10px; }
    .category-editor { display: grid; gap: 8px; margin-top: 8px; max-height: min(46vh, 520px); overflow: auto; padding-right: 2px; }
    .category-row { display: grid; grid-template-columns: minmax(120px, 180px) minmax(0, 1fr) minmax(0, 1fr) auto; gap: 8px; align-items: end; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); padding: 8px; }
    .category-field label { margin: 0 0 5px; }
    .category-row button { padding: 8px 10px; }
    .settings-status { color: var(--muted); font-size: 13px; min-height: 18px; margin-top: 10px; }
    @media (max-width: 860px) {
      .header-top { align-items: flex-start; flex-direction: column; }
      .toolbar { grid-template-columns: 1fr; }
      .dashboard { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .bulk-actions { grid-template-columns: 1fr; }
      .settings-grid, .category-row { grid-template-columns: 1fr; }
      main { grid-template-columns: 1fr; padding: 12px; }
      header { padding: 14px 12px; }
      aside { position: static; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-top">
      <h1>X Bookmarks</h1>
      <div class="ai-actions">
        <button id="settings" type="button">Settings</button>
        <button id="ai-classify" class="primary" type="button">AI classify</button>
      </div>
    </div>
    <div class="toolbar">
      <input id="query" type="search" placeholder="Search">
      <select id="category"></select>
      <select id="status">
        <option value="">All status</option>
        <option value="active">Active</option>
        <option value="unread">Unread</option>
        <option value="read">Read</option>
        <option value="important">Important</option>
        <option value="pending_review">Pending review</option>
        <option value="archived">Archived</option>
      </select>
      <button id="refresh" type="button">Refresh</button>
    </div>
    <div class="dashboard" id="sync-cards">
      <div class="metric">
        <div class="metric-label">Last sync</div>
        <div class="metric-value" id="sync-last">No sync status</div>
      </div>
      <div class="metric">
        <div class="metric-label">Imported</div>
        <div class="metric-value" id="sync-imported">0 source</div>
      </div>
      <div class="metric">
        <div class="metric-label">Classified</div>
        <div class="metric-value" id="sync-classified">0</div>
      </div>
      <div class="metric">
        <div class="metric-label">Pending review</div>
        <div class="metric-value" id="review-summary">0</div>
      </div>
    </div>
    <div id="sync" class="status"></div>
  </header>
  <main>
    <section>
      <div class="bulk-actions" data-active="false">
        <span id="bulk-count">0 selected</span>
        <button id="bulk-accept" type="button">Accept</button>
        <button id="bulk-archive" type="button">Archive</button>
        <input id="bulk-category" autocomplete="off" placeholder="Category">
        <button id="bulk-category-apply" type="button">Apply category</button>
      </div>
      <div id="list" class="list"></div>
    </section>
    <aside>
      <h2 id="editor-title">No item selected</h2>
      <div id="editor-body" hidden>
        <div class="editor-section">
          <div class="section-title">Preview</div>
          <div id="rich" class="rich"></div>
        </div>
        <div class="editor-section">
          <div class="section-title">Classification</div>
          <div id="review-detail" class="review-panel"></div>
          <label for="edit-category">Category</label>
          <input id="edit-category" autocomplete="off">
          <label for="edit-tags">Tags</label>
          <input id="edit-tags" autocomplete="off">
        </div>
        <div class="editor-section">
          <div class="section-title">Notes & State</div>
          <label for="edit-notes">Notes</label>
          <textarea id="edit-notes" rows="5"></textarea>
          <div class="checks">
            <label><input id="edit-read" type="checkbox"> Read</label>
            <label><input id="edit-important" type="checkbox"> Important</label>
            <label><input id="edit-archived" type="checkbox"> Archived</label>
          </div>
        </div>
        <div class="editor-section">
          <div class="section-title">Actions</div>
          <div class="actions">
            <button id="save" class="primary" type="button">Save</button>
            <button id="accept-review" type="button">Accept</button>
            <button id="skip-review" type="button">Skip</button>
            <button id="mark-pending" type="button">Mark pending</button>
            <button id="open" type="button">Open</button>
          </div>
        </div>
      </div>
    </aside>
  </main>
  <dialog id="ai-dialog">
    <form method="dialog" class="modal">
      <h2>AI classify</h2>
      <label for="ai-category">Category</label>
      <select id="ai-category"></select>
      <label for="ai-limit">Limit</label>
      <input id="ai-limit" type="number" min="1" max="500" step="1" value="20">
      <div class="modal-actions">
        <button id="ai-cancel" type="button">Cancel</button>
        <button id="ai-run" class="primary" type="button">Run</button>
      </div>
    </form>
  </dialog>
  <dialog id="settings-dialog" class="settings-modal">
    <form method="dialog" class="modal">
      <div class="modal-header">
        <h2>Settings</h2>
        <button id="settings-close" type="button">Close</button>
      </div>
      <div class="section-title">Ollama</div>
      <div class="settings-grid">
        <div>
          <label for="settings-ollama-url">Address and port</label>
          <input id="settings-ollama-url" autocomplete="off" placeholder="http://127.0.0.1:11434">
        </div>
        <div>
          <label for="settings-ollama-timeout">AI classify timeout</label>
          <input id="settings-ollama-timeout" type="number" min="1" max="1800" step="1">
        </div>
        <button id="settings-find-models" type="button">Find models</button>
      </div>
      <div id="settings-model-status" class="settings-status"></div>
      <div class="settings-model-row">
        <label for="settings-ollama-model">Available model</label>
        <select id="settings-ollama-model"></select>
      </div>
      <div class="section-title" style="margin-top:16px;">Categories</div>
      <div id="settings-categories" class="category-editor"></div>
      <button id="settings-add-category" type="button">Add category</button>
      <div id="settings-status" class="settings-status"></div>
      <div class="modal-actions">
        <button id="settings-cancel" type="button">Cancel</button>
        <button id="settings-save" class="primary" type="button">Save settings</button>
      </div>
    </form>
  </dialog>
  <script>
    const state = { items: [], selected: null, selectedIds: new Set(), skippedReviewIds: new Set(), aiTimer: null, settings: null, lastSyncKey: "" };
    const $ = (id) => document.getElementById(id);
    const query = $("query"), category = $("category"), status = $("status");
    const list = $("list"), sync = $("sync"), reviewSummary = $("review-summary");

    async function api(path, options) {
      const response = await fetch(path, options);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || response.statusText);
      return body;
    }

    function debounce(fn, delay) {
      let timer;
      return () => { clearTimeout(timer); timer = setTimeout(fn, delay); };
    }

    async function loadCategories() {
      const body = await api("/api/categories");
      const current = category.value;
      category.innerHTML = '<option value="">All categories</option>' +
        body.categories.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)} (${c.count})</option>`).join("");
      category.value = current;
      renderAiCategoryOptions(body.categories);
    }

    async function loadSettings() {
      state.settings = await api("/api/settings");
      renderSettingsForm();
    }

    function renderSettingsForm() {
      const settings = state.settings || { ollama: {}, categories: [] };
      $("settings-ollama-url").value = settings.ollama.url || "http://127.0.0.1:11434";
      $("settings-ollama-timeout").value = settings.ollama.timeout || 180;
      renderOllamaModelOptions(settings.ollama.models || [], settings.ollama.model || "qwen2.5:7b");
      renderSettingsCategories(settings.categories || []);
    }

    function renderOllamaModelOptions(models, selected) {
      const values = [];
      const seen = new Set();
      for (const value of [selected, ...(models || [])]) {
        const model = String(value || "").trim();
        if (!model || seen.has(model)) continue;
        values.push(model);
        seen.add(model);
      }
      $("settings-ollama-model").innerHTML = values.map(model => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("");
      if (selected && seen.has(selected)) $("settings-ollama-model").value = selected;
    }

    function renderSettingsCategories(categories) {
      $("settings-categories").innerHTML = categories.map((category, index) => `
        <div class="category-row" data-index="${index}">
          <div class="category-field">
            <label>Category name</label>
            <input class="settings-category-name" autocomplete="off" placeholder="Linux" value="${escapeHtml(category.name || "")}">
          </div>
          <div class="category-field">
            <label>Description for AI</label>
            <input class="settings-category-description" autocomplete="off" placeholder="What this category means" value="${escapeHtml(category.description || "")}">
          </div>
          <div class="category-field">
            <label>Rule keywords</label>
            <input class="settings-category-keywords" autocomplete="off" placeholder="linux, shell, terminal" value="${escapeHtml((category.keywords || []).join(", "))}">
          </div>
          <button class="settings-delete-category danger" type="button">Delete</button>
        </div>
      `).join("");
      for (const node of $("settings-categories").querySelectorAll(".settings-delete-category")) {
        node.addEventListener("click", () => {
          const row = node.closest(".category-row");
          row.remove();
        });
      }
    }

    function openSettingsDialog() {
      $("settings-status").textContent = "";
      if (state.settings) renderSettingsForm();
      $("settings-dialog").showModal();
    }

    function closeSettingsDialog() {
      $("settings-dialog").close();
    }

    function addSettingsCategory() {
      const row = document.createElement("div");
      row.className = "category-row";
      row.innerHTML = `
        <div class="category-field">
          <label>Category name</label>
          <input class="settings-category-name" autocomplete="off" placeholder="Linux">
        </div>
        <div class="category-field">
          <label>Description for AI</label>
          <input class="settings-category-description" autocomplete="off" placeholder="What this category means">
        </div>
        <div class="category-field">
          <label>Rule keywords</label>
          <input class="settings-category-keywords" autocomplete="off" placeholder="linux, shell, terminal">
        </div>
        <button class="settings-delete-category danger" type="button">Delete</button>
      `;
      row.querySelector(".settings-delete-category").addEventListener("click", () => row.remove());
      $("settings-categories").appendChild(row);
      row.querySelector(".settings-category-name").focus();
    }

    function collectSettingsPayload() {
      const categories = Array.from($("settings-categories").querySelectorAll(".category-row")).map(row => ({
        name: row.querySelector(".settings-category-name").value.trim(),
        description: row.querySelector(".settings-category-description").value.trim(),
        keywords: row.querySelector(".settings-category-keywords").value.split(",").map(value => value.trim()).filter(Boolean)
      })).filter(category => category.name);
      return {
        ollama: {
          url: $("settings-ollama-url").value.trim() || "http://127.0.0.1:11434",
          model: $("settings-ollama-model").value.trim() || "qwen2.5:7b",
          timeout: Number($("settings-ollama-timeout").value || 180)
        },
        categories
      };
    }

    function collectOllamaPayload() {
      return {
        ollama: {
          url: $("settings-ollama-url").value.trim() || "http://127.0.0.1:11434",
          model: $("settings-ollama-model").value.trim() || "qwen2.5:7b"
        }
      };
    }

    async function findOllamaModels() {
      $("settings-model-status").textContent = "Finding models...";
      $("settings-find-models").disabled = true;
      try {
        const body = await api("/api/settings/ollama-models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(collectOllamaPayload())
        });
        renderOllamaModelOptions(body.models || [], body.selected || (body.models && body.models[0]) || "");
        $("settings-model-status").textContent = body.models && body.models.length
          ? `Found ${body.models.length} model(s) · discovery timeout ${body.ollama.timeout}s`
          : "No models returned";
      } catch (error) {
        $("settings-model-status").textContent = `Find models failed: ${error.message}`;
      } finally {
        $("settings-find-models").disabled = false;
      }
    }

    async function saveSettings() {
      $("settings-status").textContent = "Saving settings...";
      $("settings-save").disabled = true;
      try {
        state.settings = await api("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(collectSettingsPayload())
        });
        renderSettingsForm();
        await Promise.all([loadCategories(), loadItems()]);
        $("settings-status").textContent = "Settings saved";
        sync.textContent = "Settings saved";
      } catch (error) {
        $("settings-status").textContent = `Save failed: ${error.message}`;
      } finally {
        $("settings-save").disabled = false;
      }
    }

    function renderAiCategoryOptions(categories) {
      const current = $("ai-category").value || category.value;
      $("ai-category").innerHTML = '<option value="">Unclassified only</option>' +
        categories.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)} (${c.count})</option>`).join("");
      $("ai-category").value = current;
    }

    async function loadSyncStatus() {
      const body = await api("/api/sync-status");
      const summary = body.summary;
      if (!summary) {
        $("sync-last").textContent = "No sync status";
        $("sync-imported").textContent = "0 source";
        $("sync-classified").textContent = "0";
        sync.textContent = "";
        return null;
      }
      const more = summary.has_more ? " · more pages available" : "";
      $("sync-last").textContent = `${summary.status} · ${summary.finished_at || "unfinished"}`;
      $("sync-imported").textContent = `${summary.source_count} source · ${summary.inserted} new · ${summary.updated} updated`;
      $("sync-classified").textContent = `${summary.classified} · exported ${summary.exported}`;
      sync.textContent = `${summary.connector || "unknown"} · unchanged ${summary.unchanged} · pages ${summary.pages_fetched}${more}`;
      return summary;
    }

    function syncKey(summary) {
      if (!summary) return "";
      return `${summary.id || ""}:${summary.status || ""}:${summary.finished_at || ""}:${summary.imported || 0}:${summary.exported || 0}`;
    }

    async function refreshAll() {
      const [, summary] = await Promise.all([loadCategories(), loadSyncStatus(), loadReviewSummary(), loadItems()]);
      state.lastSyncKey = syncKey(summary);
    }

    async function pollExternalChanges() {
      if (document.visibilityState !== "visible") return;
      try {
        const summary = await loadSyncStatus();
        const key = syncKey(summary);
        if (key && state.lastSyncKey && key !== state.lastSyncKey) {
          state.lastSyncKey = key;
          await Promise.all([loadCategories(), loadReviewSummary(), loadItems()]);
          sync.textContent = `${sync.textContent} · refreshed`;
        } else if (key && !state.lastSyncKey) {
          state.lastSyncKey = key;
        }
      } catch (error) {
        sync.textContent = error.message;
      }
    }

    async function loadReviewSummary() {
      const body = await api("/api/review-summary");
      const reasons = body.by_reason || {};
      reviewSummary.textContent = `${body.pending || 0} · low-confidence ${reasons["low-confidence"] || 0} · changed ${reasons["content-changed"] || 0}`;
    }

    async function loadItems() {
      const params = new URLSearchParams({ limit: "100" });
      if (query.value.trim()) params.set("query", query.value.trim());
      if (category.value) params.set("category", category.value);
      if (status.value) params.set("status", status.value);
      const body = await api(`/api/bookmarks?${params}`);
      state.items = body.items.filter(item => !state.skippedReviewIds.has(item.tweet_id));
      pruneSelectedIds();
      renderList();
    }

    function renderList() {
      if (!state.items.length) {
        list.innerHTML = '<div class="empty">No bookmarks</div>';
        selectItem(null);
        renderBulkState();
        return;
      }
      list.innerHTML = state.items.map(item => `
        <article class="item" data-id="${escapeHtml(item.tweet_id)}" aria-selected="${state.selected && state.selected.tweet_id === item.tweet_id}">
          <div class="item-head">
            <input class="bulk-check" type="checkbox" data-id="${escapeHtml(item.tweet_id)}" aria-label="Select ${escapeHtml(item.tweet_id)}" ${state.selectedIds.has(item.tweet_id) ? "checked" : ""}>
            <div class="item-title">
              <div class="item-author">${escapeHtml(authorLabel(item))}</div>
              <div class="item-date">${escapeHtml(item.created_at || "Unknown date")} · ${escapeHtml(item.tweet_id)}</div>
            </div>
            <a class="item-open" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Open</a>
          </div>
          <div class="text">${escapeHtml(trimText(item.text, 260))}</div>
          ${item.review_state === "pending" ? `<div class="note">Review: ${escapeHtml(reviewReasonLabel(item.review_reason))}${item.confidence != null ? ` · confidence ${escapeHtml(formatConfidence(item.confidence))}` : ""}</div>` : ""}
          ${item.notes ? `<div class="note">Note: ${escapeHtml(trimText(item.notes, 180))}</div>` : ""}
          ${statusBadgesHtml(item)}
          ${mediaPreviewHtml(item)}
          ${badgesHtml(item)}
          <div class="tags">${item.tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
        </article>
      `).join("");
      for (const node of list.querySelectorAll(".item")) {
        node.addEventListener("click", () => selectItem(state.items.find(item => item.tweet_id === node.dataset.id)));
      }
      for (const node of list.querySelectorAll(".bulk-check")) {
        node.addEventListener("click", event => event.stopPropagation());
        node.addEventListener("change", () => toggleBulkSelection(node.dataset.id, node.checked));
      }
      for (const node of list.querySelectorAll(".item-open")) {
        node.addEventListener("click", event => event.stopPropagation());
      }
      if (state.selected) {
        const fresh = state.items.find(item => item.tweet_id === state.selected.tweet_id);
        selectItem(fresh || state.items[0]);
      } else {
        selectItem(state.items[0]);
      }
      renderBulkState();
    }

    function selectItem(item) {
      state.selected = item;
      for (const node of list.querySelectorAll(".item")) {
        node.setAttribute("aria-selected", String(item && node.dataset.id === item.tweet_id));
      }
      $("editor-title").textContent = item ? item.tweet_id : "No item selected";
      $("editor-body").hidden = !item;
      if (!item) return;
      $("review-detail").innerHTML = reviewDetailHtml(item);
      $("rich").innerHTML = richHtml(item);
      $("edit-category").value = item.category === "Unclassified" ? "" : item.category;
      $("edit-tags").value = item.tags.join(", ");
      $("edit-notes").value = item.notes || "";
      $("edit-read").checked = item.read_state === "read";
      $("edit-important").checked = item.important;
      $("edit-archived").checked = item.archived;
      $("accept-review").disabled = item.review_state !== "pending";
      $("skip-review").disabled = item.review_state !== "pending";
      $("mark-pending").disabled = item.review_state === "pending";
    }

    function toggleBulkSelection(tweetId, selected) {
      if (!tweetId) return;
      if (selected) state.selectedIds.add(tweetId);
      else state.selectedIds.delete(tweetId);
      renderBulkState();
    }

    function pruneSelectedIds() {
      const visibleIds = new Set(state.items.map(item => item.tweet_id));
      for (const tweetId of Array.from(state.selectedIds)) {
        if (!visibleIds.has(tweetId)) state.selectedIds.delete(tweetId);
      }
    }

    function renderBulkState() {
      const count = state.selectedIds.size;
      $("bulk-count").textContent = `${count} selected`;
      document.querySelector(".bulk-actions").dataset.active = String(count > 0);
      $("bulk-accept").disabled = count === 0;
      $("bulk-archive").disabled = count === 0;
      $("bulk-category-apply").disabled = count === 0;
    }

    function authorLabel(item) {
      const author = item.author_profile || {};
      if (author.screen_name && author.name) return `${author.name} (@${author.screen_name})`;
      if (author.screen_name) return `@${author.screen_name}`;
      if (author.name) return author.name;
      return item.author || author.user_id || "Unknown";
    }

    function badgesHtml(item) {
      const badges = [];
      if (item.media && item.media.length) badges.push(`${item.media.length} media`);
      if (item.card) badges.push("card");
      if (item.quoted_tweet) badges.push("quote");
      if (!badges.length) return "";
      return `<div class="badges">${badges.map(value => `<span class="badge">${escapeHtml(value)}</span>`).join("")}</div>`;
    }

    function statusBadgesHtml(item) {
      const badges = [
        ["category", item.category || "General"],
        [item.read_state === "read" ? "read" : "", item.read_state || "unread"]
      ];
      if (item.important) badges.push(["important", "important"]);
      if (item.review_state === "pending") badges.push(["pending", "pending review"]);
      if (item.archived) badges.push(["archived", "archived"]);
      return `<div class="badges">${badges.map(([kind, value]) => `<span class="badge ${escapeHtml(kind)}">${escapeHtml(value)}</span>`).join("")}</div>`;
    }

    function mediaPreviewHtml(item) {
      if (!item.media || !item.media.length) return "";
      const nodes = item.media.slice(0, 4).map(media => {
        const label = escapeHtml(media.alt_text || media.type || "media");
        if (media.type === "photo") {
          return `<img src="${escapeHtml(media.url)}" alt="${label}" loading="lazy">`;
        }
        return `<a href="${escapeHtml(media.url)}" target="_blank" rel="noopener">${escapeHtml(media.type || "media")}</a>`;
      });
      return `<div class="item-media">${nodes.join("")}</div>`;
    }

    function reviewReasonLabel(value) {
      const labels = {
        "new-import": "new import",
        "low-confidence": "low confidence",
        "content-changed": "content changed",
        "manual-pending": "manual pending"
      };
      return labels[value] || value || "pending";
    }

    function formatConfidence(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return "unknown";
      return number.toFixed(2);
    }

    function reviewDetailHtml(item) {
      const rows = [
        ["Review", item.review_state === "pending" ? `pending (${reviewReasonLabel(item.review_reason)})` : "accepted"],
        ["Provider", item.provider || "unknown"],
        ["Confidence", item.confidence == null ? "unknown" : formatConfidence(item.confidence)],
        ["Reason", item.reason || ""]
      ].filter(([, value]) => value);
      return `<strong>Review</strong>${rows.map(([name, value]) => `<div><span class="meta">${escapeHtml(name)}:</span> ${escapeHtml(value)}</div>`).join("")}`;
    }

    function richHtml(item) {
      return [authorHtml(item), mediaHtml(item), cardHtml(item), quoteHtml(item)].filter(Boolean).join("") || '<div class="empty">No preview</div>';
    }

    function authorHtml(item) {
      const author = item.author_profile;
      if (!author) return "";
      const parts = [];
      if (author.user_id) parts.push(`id ${author.user_id}`);
      if (author.followers_count) parts.push(`${author.followers_count} followers`);
      if (author.verified) parts.push("verified");
      const avatar = author.profile_image_url ? `<img src="${escapeHtml(author.profile_image_url)}" alt="" loading="lazy" style="width:32px;height:32px;border-radius:50%;float:right;">` : "";
      return `<section class="author-box">${avatar}<strong>${escapeHtml(authorLabel(item))}</strong><div class="meta">${escapeHtml(parts.join(" · "))}</div></section>`;
    }

    function mediaHtml(item) {
      if (!item.media || !item.media.length) return "";
      const nodes = item.media.map(media => {
        if (media.type === "photo") {
          return `<img src="${escapeHtml(media.url)}" alt="${escapeHtml(media.alt_text || "media")}" loading="lazy">`;
        }
        return `<a href="${escapeHtml(media.url)}" target="_blank" rel="noopener">${escapeHtml(media.type || "media")}</a>`;
      });
      return `<section class="media-grid">${nodes.join("")}</section>`;
    }

    function cardHtml(item) {
      const card = item.card;
      if (!card) return "";
      const title = escapeHtml(card.title || "Linked card");
      const titleHtml = card.url ? `<a href="${escapeHtml(card.url)}" target="_blank" rel="noopener">${title}</a>` : title;
      const desc = card.description ? `<p>${escapeHtml(trimText(card.description, 220))}</p>` : "";
      return `<section class="link-card"><strong>${titleHtml}</strong>${desc}</section>`;
    }

    function quoteHtml(item) {
      const quote = item.quoted_tweet;
      if (!quote) return "";
      const author = quote.author || {};
      const name = author.screen_name ? `@${author.screen_name}` : (author.name || "Quoted tweet");
      const title = quote.url ? `<a href="${escapeHtml(quote.url)}" target="_blank" rel="noopener">${escapeHtml(name)}</a>` : escapeHtml(name);
      const text = quote.text ? `<p>${escapeHtml(trimText(quote.text, 260))}</p>` : "";
      return `<section class="quote-box"><strong>${title}</strong>${text}</section>`;
    }

    async function saveSelected() {
      if (!state.selected) return;
      sync.textContent = "Saving...";
      const payload = {
        category: $("edit-category").value.trim(),
        tags: $("edit-tags").value.split(",").map(v => v.trim()).filter(Boolean),
        notes: $("edit-notes").value,
        read_state: $("edit-read").checked ? "read" : "unread",
        important: $("edit-important").checked,
        archived: $("edit-archived").checked
      };
      try {
        const body = await api(`/api/bookmarks/${encodeURIComponent(state.selected.tweet_id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        state.selected = body.item;
        await Promise.all([loadCategories(), loadReviewSummary(), loadItems()]);
        sync.textContent = `Saved ${state.selected.tweet_id}`;
      } catch (error) {
        sync.textContent = `Save failed: ${error.message}`;
      }
    }

    async function acceptSelected() {
      if (!state.selected) return;
      const acceptedId = state.selected.tweet_id;
      sync.textContent = "Accepting...";
      try {
        const body = await api(`/api/bookmarks/${encodeURIComponent(state.selected.tweet_id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ review_state: "accepted" })
        });
        state.selected = body.item;
        await Promise.all([loadCategories(), loadReviewSummary(), loadItems()]);
        sync.textContent = `Accepted ${acceptedId}`;
      } catch (error) {
        sync.textContent = `Accept failed: ${error.message}`;
      }
    }

    async function markPendingSelected() {
      if (!state.selected) return;
      const pendingId = state.selected.tweet_id;
      sync.textContent = "Marking pending...";
      try {
        const body = await api(`/api/bookmarks/${encodeURIComponent(pendingId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ review_state: "pending" })
        });
        state.selected = body.item;
        await Promise.all([loadCategories(), loadReviewSummary(), loadItems()]);
        sync.textContent = `Marked pending ${pendingId}`;
      } catch (error) {
        sync.textContent = `Mark pending failed: ${error.message}`;
      }
    }

    async function bulkUpdateSelected(action) {
      const tweetIds = Array.from(state.selectedIds);
      if (!tweetIds.length) return;
      const payload = { action, tweet_ids: tweetIds };
      if (action === "category") {
        const categoryValue = $("bulk-category").value.trim();
        if (!categoryValue) {
          sync.textContent = "Bulk category failed: category is required";
          return;
        }
        payload.category = categoryValue;
      }
      sync.textContent = "Updating selected...";
      try {
        const body = await api("/api/bookmarks/bulk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        state.selectedIds.clear();
        await Promise.all([loadCategories(), loadReviewSummary(), loadItems()]);
        sync.textContent = `Updated ${body.updated} selected bookmark(s)${body.missing && body.missing.length ? ` · missing ${body.missing.length}` : ""}`;
      } catch (error) {
        sync.textContent = `Bulk update failed: ${error.message}`;
      }
    }

    function openAiDialog() {
      $("ai-category").value = category.value || "";
      $("ai-limit").value = $("ai-limit").value || "20";
      $("ai-dialog").showModal();
    }

    function closeAiDialog() {
      $("ai-dialog").close();
    }

    async function runAiClassify() {
      const limitValue = Number($("ai-limit").value || 20);
      if (!Number.isInteger(limitValue) || limitValue < 1 || limitValue > 500) {
        sync.textContent = "AI classify failed: limit must be between 1 and 500";
        return;
      }
      const targetCategory = $("ai-category").value || "";
      $("ai-classify").disabled = true;
      $("ai-run").disabled = true;
      closeAiDialog();
      startAiProgress(targetCategory, limitValue);
      try {
        const body = await api("/api/classify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: "ollama",
            category: targetCategory || null,
            limit: limitValue,
            reclassify: Boolean(targetCategory),
            export_html: true,
            ollama_url: state.settings && state.settings.ollama ? state.settings.ollama.url : undefined,
            ollama_model: state.settings && state.settings.ollama ? state.settings.ollama.model : undefined,
            ollama_timeout: state.settings && state.settings.ollama ? state.settings.ollama.timeout : undefined
          })
        });
        stopAiProgress();
        await Promise.all([loadCategories(), loadSyncStatus(), loadReviewSummary(), loadItems()]);
        sync.textContent = `AI classified ${body.classified} bookmark(s)${body.category ? ` in ${body.category}` : ""} · exported ${body.exported}`;
      } catch (error) {
        stopAiProgress();
        sync.textContent = `AI classify failed: ${error.message}`;
      } finally {
        $("ai-classify").disabled = false;
        $("ai-run").disabled = false;
      }
    }

    function startAiProgress(targetCategory, limitValue) {
      stopAiProgress();
      const started = Date.now();
      const target = targetCategory || "unclassified bookmarks";
      const render = () => {
        const elapsed = Math.max(0, Math.round((Date.now() - started) / 1000));
        sync.textContent = `AI classifying ${target} (${limitValue})... ${elapsed}s elapsed`;
      };
      render();
      state.aiTimer = setInterval(render, 1000);
    }

    function stopAiProgress() {
      if (state.aiTimer) {
        clearInterval(state.aiTimer);
        state.aiTimer = null;
      }
    }

    function skipSelected() {
      if (!state.selected || state.selected.review_state !== "pending") return;
      const skippedId = state.selected.tweet_id;
      state.skippedReviewIds.add(skippedId);
      state.items = state.items.filter(item => item.tweet_id !== skippedId);
      renderList();
      sync.textContent = `Skipped ${skippedId}`;
    }

    function resetSkippedReviewIds() {
      state.skippedReviewIds.clear();
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }

    function trimText(value, max) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      return text.length > max ? text.slice(0, max - 1) + "..." : text;
    }

    query.addEventListener("input", debounce(() => { resetSkippedReviewIds(); loadItems(); }, 250));
    category.addEventListener("change", () => { resetSkippedReviewIds(); loadItems(); });
    status.addEventListener("change", () => { resetSkippedReviewIds(); loadItems(); });
    $("refresh").addEventListener("click", () => { resetSkippedReviewIds(); return refreshAll(); });
    $("save").addEventListener("click", saveSelected);
    $("accept-review").addEventListener("click", acceptSelected);
    $("skip-review").addEventListener("click", skipSelected);
    $("mark-pending").addEventListener("click", markPendingSelected);
    $("bulk-accept").addEventListener("click", () => bulkUpdateSelected("accept"));
    $("bulk-archive").addEventListener("click", () => bulkUpdateSelected("archive"));
    $("bulk-category-apply").addEventListener("click", () => bulkUpdateSelected("category"));
    $("settings").addEventListener("click", openSettingsDialog);
    $("settings-close").addEventListener("click", closeSettingsDialog);
    $("settings-cancel").addEventListener("click", closeSettingsDialog);
    $("settings-add-category").addEventListener("click", addSettingsCategory);
    $("settings-find-models").addEventListener("click", findOllamaModels);
    $("settings-save").addEventListener("click", saveSettings);
    $("ai-classify").addEventListener("click", openAiDialog);
    $("ai-cancel").addEventListener("click", closeAiDialog);
    $("ai-run").addEventListener("click", runAiClassify);
    $("open").addEventListener("click", () => { if (state.selected) window.open(state.selected.url, "_blank", "noopener"); });

    Promise.all([loadSettings(), loadCategories(), loadSyncStatus(), loadReviewSummary(), loadItems()]).then(([, , summary]) => {
      state.lastSyncKey = syncKey(summary);
      window.setInterval(pollExternalChanges, 5000);
    }).catch(error => {
      sync.textContent = error.message;
    });
  </script>
</body>
</html>
"""
