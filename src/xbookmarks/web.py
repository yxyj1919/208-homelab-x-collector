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
        if parsed.path == "/api/sync-status":
            self._handle_sync_status()
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

    def _handle_sync_status(self) -> None:
        self._send_json(self.bookmark_service.sync_status(latest_limit=5))

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
    :root { color-scheme: light; --bg: #f7f8fa; --panel: #ffffff; --text: #1f2933; --muted: #667085; --line: #d8dee8; --accent: #0f766e; --danger: #b42318; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    header { padding: 18px 24px 12px; border-bottom: 1px solid var(--line); background: #fff; position: sticky; top: 0; z-index: 5; }
    h1 { margin: 0 0 12px; font-size: 22px; font-weight: 650; letter-spacing: 0; }
    .toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) 180px 160px auto; gap: 10px; align-items: center; }
    input, select, textarea, button { font: inherit; }
    input, select, textarea { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; background: #fff; color: var(--text); }
    button { border: 1px solid var(--line); border-radius: 6px; padding: 9px 12px; background: #fff; color: var(--text); cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.danger { color: var(--danger); }
    main { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; padding: 16px 24px; }
    .status { color: var(--muted); font-size: 13px; margin-top: 10px; min-height: 18px; }
    .list { display: grid; gap: 8px; align-content: start; }
    .item { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 12px; cursor: pointer; }
    .item[aria-selected="true"] { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
    .meta, .tags, .sync { color: var(--muted); font-size: 13px; line-height: 1.4; }
    .text { white-space: pre-wrap; margin: 8px 0; line-height: 1.45; }
    .note { color: #344054; background: #f2f4f7; border-left: 3px solid var(--accent); margin-top: 8px; padding: 7px 9px; font-size: 13px; line-height: 1.4; }
    .tag { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; margin: 4px 4px 0 0; background: #f9fafb; }
    .badges { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
    .badge { border: 1px solid var(--line); border-radius: 4px; color: var(--muted); background: #fff; font-size: 12px; padding: 1px 6px; }
    aside { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; align-self: start; position: sticky; top: 100px; }
    aside h2 { margin: 0 0 12px; font-size: 17px; letter-spacing: 0; }
    .rich { display: grid; gap: 10px; margin-bottom: 12px; }
    .author-box, .link-card, .quote-box { border: 1px solid var(--line); border-radius: 8px; background: #f9fafb; padding: 10px; }
    .author-box strong, .link-card strong, .quote-box strong { display: block; margin-bottom: 4px; }
    .media-grid { display: grid; gap: 8px; }
    .media-grid img { width: 100%; max-height: 280px; object-fit: contain; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    .link-card p, .quote-box p { margin: 5px 0 0; color: #344054; font-size: 13px; line-height: 1.4; }
    label { display: block; color: var(--muted); font-size: 13px; margin: 12px 0 5px; }
    .checks { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
    .checks label { display: inline-flex; align-items: center; gap: 6px; margin: 0; color: var(--text); }
    .checks input { width: auto; }
    .actions { display: flex; gap: 8px; margin-top: 12px; }
    .empty { color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; padding: 24px; text-align: center; }
    @media (max-width: 860px) {
      .toolbar { grid-template-columns: 1fr; }
      main { grid-template-columns: 1fr; padding: 12px; }
      header { padding: 14px 12px; }
      aside { position: static; }
    }
  </style>
</head>
<body>
  <header>
    <h1>X Bookmarks</h1>
    <div class="toolbar">
      <input id="query" type="search" placeholder="Search">
      <select id="category"></select>
      <select id="status">
        <option value="">All status</option>
        <option value="active">Active</option>
        <option value="unread">Unread</option>
        <option value="read">Read</option>
        <option value="important">Important</option>
        <option value="archived">Archived</option>
      </select>
      <button id="refresh" type="button">Refresh</button>
    </div>
    <div id="sync" class="status"></div>
  </header>
  <main>
    <section>
      <div id="list" class="list"></div>
    </section>
    <aside>
      <h2 id="editor-title">No item selected</h2>
      <div id="editor-body" hidden>
        <div id="rich" class="rich"></div>
        <label for="edit-category">Category</label>
        <input id="edit-category" autocomplete="off">
        <label for="edit-tags">Tags</label>
        <input id="edit-tags" autocomplete="off">
        <label for="edit-notes">Notes</label>
        <textarea id="edit-notes" rows="5"></textarea>
        <div class="checks">
          <label><input id="edit-read" type="checkbox"> Read</label>
          <label><input id="edit-important" type="checkbox"> Important</label>
          <label><input id="edit-archived" type="checkbox"> Archived</label>
        </div>
        <div class="actions">
          <button id="save" class="primary" type="button">Save</button>
          <button id="open" type="button">Open</button>
        </div>
      </div>
    </aside>
  </main>
  <script>
    const state = { items: [], selected: null };
    const $ = (id) => document.getElementById(id);
    const query = $("query"), category = $("category"), status = $("status");
    const list = $("list"), sync = $("sync");

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
    }

    async function loadSyncStatus() {
      const body = await api("/api/sync-status");
      const summary = body.summary;
      if (!summary) {
        sync.textContent = "No sync status";
        return;
      }
      const more = summary.has_more ? " · more pages available" : "";
      sync.textContent = `Last sync: ${summary.status} · ${summary.connector || "unknown"} · source ${summary.source_count} · inserted ${summary.inserted} · updated ${summary.updated} · unchanged ${summary.unchanged} · classified ${summary.classified} · exported ${summary.exported} · pages ${summary.pages_fetched}${more} · ${summary.finished_at || ""}`;
    }

    async function loadItems() {
      const params = new URLSearchParams({ limit: "100" });
      if (query.value.trim()) params.set("query", query.value.trim());
      if (category.value) params.set("category", category.value);
      if (status.value) params.set("status", status.value);
      const body = await api(`/api/bookmarks?${params}`);
      state.items = body.items;
      renderList();
    }

    function renderList() {
      if (!state.items.length) {
        list.innerHTML = '<div class="empty">No bookmarks</div>';
        selectItem(null);
        return;
      }
      list.innerHTML = state.items.map(item => `
        <article class="item" data-id="${escapeHtml(item.tweet_id)}" aria-selected="${state.selected && state.selected.tweet_id === item.tweet_id}">
          <div class="meta">${escapeHtml(item.category)} · ${escapeHtml(authorLabel(item))} · ${escapeHtml(item.created_at || "Unknown date")} · ${escapeHtml(item.read_state)}${item.important ? " · important" : ""}${item.archived ? " · archived" : ""}</div>
          <div class="text">${escapeHtml(trimText(item.text, 260))}</div>
          ${item.notes ? `<div class="note">Note: ${escapeHtml(trimText(item.notes, 180))}</div>` : ""}
          ${badgesHtml(item)}
          <div class="tags">${item.tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
        </article>
      `).join("");
      for (const node of list.querySelectorAll(".item")) {
        node.addEventListener("click", () => selectItem(state.items.find(item => item.tweet_id === node.dataset.id)));
      }
      if (state.selected) {
        const fresh = state.items.find(item => item.tweet_id === state.selected.tweet_id);
        selectItem(fresh || state.items[0]);
      } else {
        selectItem(state.items[0]);
      }
    }

    function selectItem(item) {
      state.selected = item;
      for (const node of list.querySelectorAll(".item")) {
        node.setAttribute("aria-selected", String(item && node.dataset.id === item.tweet_id));
      }
      $("editor-title").textContent = item ? item.tweet_id : "No item selected";
      $("editor-body").hidden = !item;
      if (!item) return;
      $("rich").innerHTML = richHtml(item);
      $("edit-category").value = item.category === "Unclassified" ? "" : item.category;
      $("edit-tags").value = item.tags.join(", ");
      $("edit-notes").value = item.notes || "";
      $("edit-read").checked = item.read_state === "read";
      $("edit-important").checked = item.important;
      $("edit-archived").checked = item.archived;
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

    function richHtml(item) {
      return [authorHtml(item), mediaHtml(item), cardHtml(item), quoteHtml(item)].filter(Boolean).join("");
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
        await Promise.all([loadCategories(), loadItems()]);
        sync.textContent = `Saved ${state.selected.tweet_id}`;
      } catch (error) {
        sync.textContent = `Save failed: ${error.message}`;
      }
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }

    function trimText(value, max) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      return text.length > max ? text.slice(0, max - 1) + "..." : text;
    }

    query.addEventListener("input", debounce(loadItems, 250));
    category.addEventListener("change", loadItems);
    status.addEventListener("change", loadItems);
    $("refresh").addEventListener("click", () => Promise.all([loadCategories(), loadSyncStatus(), loadItems()]));
    $("save").addEventListener("click", saveSelected);
    $("open").addEventListener("click", () => { if (state.selected) window.open(state.selected.url, "_blank", "noopener"); });

    Promise.all([loadCategories(), loadSyncStatus(), loadItems()]).catch(error => {
      sync.textContent = error.message;
    });
  </script>
</body>
</html>
"""
