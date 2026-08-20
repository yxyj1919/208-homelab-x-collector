# homelab-x-collector

Local-first X/Twitter bookmark archiver.

This README focuses on the 2.0 alpha workflow: local Web UI, Chrome extension,
and SQLite storage. The historical 1.0 JSON-import workflow is documented in
[V1.0 release note](RELEASE_NOTES_V1.0.md).

## 2.0 Alpha Scope

The 2.0 alpha release turns X bookmarks into a local, reviewable archive
workflow:

- Use a Chrome extension with the active X.com browser session.
- Fetch X.com bookmarks through the bookmarks GraphQL pagination used by the
  current web session.
- Import bookmarks into a local SQLite database.
- Review, search, filter, edit, bulk categorize, accept, and archive bookmarks
  in the Web UI.
- Classify with local rules, with optional Ollama-based AI classification.
- Export an HTML archive and keep raw JSON backup data.
- Download a full JSON export file locally from the extension.

Supported in 2.0 alpha:

- Local Web UI.
- Locally loaded unpacked Chrome extension.
- Background GraphQL export in the extension, so the export can continue after
  switching browser tabs.
- Extension progress synced to the Web UI: gray idle, blinking green while
  fetching/importing, solid green for 12 seconds after completion, red on
  failure.
- Chrome notifications for completed or failed extension exports.
- JSON backup download from the extension.
- Web UI settings for Ollama address, model, AI classification timeout, and
  category rules.
- Bulk actions in the Web UI, including category selection from a dropdown.
- Bookmark list sorted newest first.

Current limits:

- The extension does not log in to X. You must already be logged in to X in
  Chrome.
- The extension depends on X.com internal GraphQL behavior and the current
  browser session. X.com changes may require updates.
- This is a local single-user tool, not a multi-user hosted service.
- X.com cookies, headers, and tokens are not written to project files, SQLite,
  run logs, or the HTML archive.

## Quick Start

Recommended first-run flow for 2.0 alpha:

1. Initialize the local project data:

   ```bash
   PYTHONPATH=src python3 -m xbookmarks.cli init --write-categories
   ```

2. Start the Web UI:

   ```bash
   PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite web --host 127.0.0.1 --port 8766
   ```

3. Open `http://127.0.0.1:8766/`.
4. Load `extension/chrome` as an unpacked Chrome extension.
5. In the extension `Option` page, set the Local UI URL to
   `http://127.0.0.1:8766`.
6. Open and refresh `https://x.com/i/bookmarks`, then confirm the extension
   popup shows `User`, `Auth`, and `Bookmarks queryId` as captured.
7. Click the extension button that exports all bookmarks to the local API.

## New User Setup

This section is for new users. The recommended workflow is the local Web UI plus
the Chrome extension.

### 1. Initialize Local Data

Run this command from the project directory:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli init --write-categories
```

By default this creates:

- SQLite database: `data/bookmarks.sqlite`
- Category configuration: `config/categories.yaml`

### 2. Start The Local Web UI

Use port `8766` so the Web UI and extension configuration match:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite web --host 127.0.0.1 --port 8766
```

Then open:

```text
http://127.0.0.1:8766/
```

The Web UI supports bookmark viewing, search, filtering, bulk category updates,
archiving, and editing.

### 3. Load The Chrome Extension

The extension directory is:

```text
extension/chrome
```

Load it in Chrome:

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select the project `extension/chrome` directory.
5. If extension code or permissions change later, return to
   `chrome://extensions` and click `Reload`.

### 4. Configure The Extension API URL

Click the `X Bookmarks Local Helper` icon in the Chrome toolbar.

Open:

```text
Option
```

Set the Local UI URL to:

```text
http://127.0.0.1:8766
```

Click `Save`.

### 5. Capture The X Session

Log in to X in the same Chrome profile, then open:

```text
https://x.com/i/bookmarks
```

Refresh the page once, then open the extension popup. Confirm that
`X Session Capture` shows these fields as captured:

- `User`
- `Auth`
- `Bookmarks queryId`

If any field is not captured, confirm that Chrome is logged in to X and refresh
`https://x.com/i/bookmarks` again.

### 6. Export Bookmarks To The Local App

Confirm that the local Web UI is running and the extension Local UI URL points
to `http://127.0.0.1:8766`.

In the extension popup, click the button that exports all bookmarks to the local
API.

Export behavior:

- The extension background worker fetches X.com bookmarks through GraphQL pages.
- While fetching, the Web UI `Import status` indicator blinks green and shows
  the current page and fetched count.
- After fetching completes, the extension submits the complete payload to
  `POST /api/extension/bookmarks`.
- The local service imports the records into SQLite, runs rule-based
  classification, and exports HTML files to `archive/`.
- After completion, the Web UI indicator stays solid green for 12 seconds and
  shows the import summary.
- Chrome shows an export completion notification.

### 7. Download A JSON Backup

To save a standalone JSON backup file, click the extension button that downloads
the export file locally.

The downloaded file contains:

- `export_metadata`
- `folders`
- `bookmarks`

### 8. Manage Bookmarks In The Web UI

Common Web UI actions:

- Search bookmarks.
- Filter by category.
- Filter by `active`, `unread`, `read`, `important`, `pending_review`, or
  `archived`.
- Edit a single bookmark category, tags, notes, read state, important flag, or
  archived flag.
- Select multiple bookmarks and apply bulk actions:
  - `Accept`
  - `Archive`
  - Select a category from the dropdown and click `Apply category`
- Use `Settings` to configure the Ollama address, model, AI classify timeout,
  and category rules.

### 9. Notes

- The extension does not log in to X. You must log in to X in Chrome first.
- The extension uses the current browser session and X.com internal GraphQL.
  Use it only with your own account and local machine.
- X.com cookies, headers, and tokens are not written to project files, SQLite,
  run logs, or the HTML archive.
- If extension permissions change, reload the extension from
  `chrome://extensions`.
- `data/`, `archive/`, `obsidian-archive/`, and `config/settings.json` are
  local runtime data and should normally stay out of Git.

Historical JSON file import, CLI step-by-step import, and Markdown/Obsidian
export workflows are documented in
[V1.0 release note](RELEASE_NOTES_V1.0.md).

## Category Rules

Default category rules are stored in `config/categories.yaml`.

Built-in categories:

- VMware
- vCenter
- VCF
- Kubernetes
- VKS
- Networking
- DevOps
- Programming
- Linux
- Data
- Tools
- Productivity
- Learning
- Career
- Language
- Finance
- Homelab
- AI
- Security
- Life
- General

You can edit `config/categories.yaml` directly to change categories and
keywords.

Configuration format:

```yaml
Tools:
  description: Software tools, websites, browser extensions, CLI utilities, online services.
  keywords:
    - tool
    - browser extension
    - singlefile
```

`description` is used in the Ollama prompt to define category boundaries.
`keywords` are used by the rule-based classifier.

You can also add or update categories from the CLI:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli category add Tools \
  --description "Software tools, websites, browser extensions, CLI utilities." \
  --keywords "tool,extension,cli"
```

`category add` can create `config/categories.yaml` if it does not already exist.
When a category already exists, the command keeps the existing `description` by
default and merges new keywords. Keywords are deduplicated case-insensitively.

Example: add a `Storage` category:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli category add Storage \
  --description "Storage systems, filesystems, NAS, and backup." \
  --keywords "storage,filesystem,nas,backup,zfs"
PYTHONPATH=src python3 -m xbookmarks.cli category list
```

After adding a category, reclassify and export existing data:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli classify --all
PYTHONPATH=src python3 -m xbookmarks.cli export-html
```

Use `--replace` if you want to fully replace an existing category description
and keyword list.

## Manual Category Fixes

You can manually update a single bookmark category after classification:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/bookmarks.sqlite set-category \
  1001 VCF \
  --tags "vcf,manual" \
  --archive-dir archive
```

`set-category` marks the record as manually categorized. Later
`classify --all` or `run --reclassify` commands do not overwrite manual
categories by default.

To allow automatic classification to overwrite manual changes, pass
`--include-manual` explicitly:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli classify --all --include-manual
```

## Ollama Provider

The default provider is local rule-based classification:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli classify --provider rules --all
```

If Ollama is installed and running locally, first reclassify only `General`
records:

```bash
ollama pull qwen2.5:7b
ollama serve
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite classify \
  --provider ollama \
  --only-category General \
  --ollama-model qwen2.5:7b \
  --ollama-timeout 180 \
  --limit 20
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite export-html \
  --archive-dir archive-real-v4-ollama
```

After verifying the first 20 results, remove `--limit` to classify all
remaining `General` records.

If the current model is slow, test a smaller model first, such as `qwen2.5:3b`
or `llama3.2:3b`.

### Remote Ollama

On the remote machine, make Ollama listen on a non-loopback address:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
ollama pull qwen2.5:7b
```

From the client machine, check connectivity and model availability first:

```bash
export OLLAMA_BASE_URL=http://192.0.2.10:11434
export OLLAMA_MODEL=qwen2.5:7b
export OLLAMA_TIMEOUT=180
PYTHONPATH=src python3 -m xbookmarks.cli ollama-check
```

After `status=available`, run classification:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite classify \
  --provider ollama \
  --only-category General \
  --limit 20
```

You can also pass the Ollama connection settings directly:

```bash
PYTHONPATH=src python3 -m xbookmarks.cli --db data/real-v4.sqlite classify \
  --provider ollama \
  --ollama-url http://192.0.2.10:11434 \
  --ollama-model qwen2.5:7b \
  --ollama-timeout 180 \
  --only-category General \
  --limit 20
```
