# X Bookmarks Chrome Extension Prototype

This is a minimal Manifest V3 prototype for the local-first xbookmarks workflow.

Current scope:

- Loads as an unpacked Chrome extension.
- Detects whether the active tab is an X/Twitter bookmarks page, tweet page, or other page.
- Checks whether the local xbookmarks Web UI is reachable.
- Opens the local Web UI.
- Adds a small draggable `XB` bubble on `x.com` and `twitter.com`.
- Captures visible, already-loaded tweet cards from the current X/Twitter page and imports them into the local SQLite database.
- Auto-scrolls the X/Twitter bookmarks page, captures loaded tweet cards, classifies them with local rules, and exports HTML to `archive/`.
- Captures local X.com session readiness state for a future GraphQL exporter: user ID, CSRF cookie availability, selected GraphQL request headers, and bookmarks query ID.
- Uses captured local X.com session state to page through the bookmarks GraphQL endpoint and import results directly into the local xbookmarks API.
- Stores only local extension settings in `chrome.storage.local`.

Out of scope for this prototype:

- Logging in to X/Twitter.
- Calling the X API.
- Writing X.com cookie/header/token values to project files, SQLite, run logs, or exported HTML.

Load locally:

1. Start the local UI:

   ```bash
   PYTHONPATH=src python3 -m xbookmarks.cli web --host 127.0.0.1 --port 8765
   ```

2. Open `chrome://extensions`.
3. Enable Developer mode.
4. Choose Load unpacked.
5. Select this directory: `extension/chrome`.

The default local UI URL is `http://127.0.0.1:8765`. Change it from the extension options page if needed.

Visible capture:

1. Open `https://x.com/i/bookmarks`.
2. Scroll until the bookmarks you want are loaded in the page.
3. Open the extension popup.
4. Click Capture visible.

This prototype depends on the current X/Twitter DOM. It captures only visible loaded tweet cards; it does not access browser login data or call private X APIs.

Automatic page capture:

1. Open `https://x.com/i/bookmarks`.
2. Open the extension popup.
3. Click Capture all.

The extension scrolls the page and imports the loaded tweet cards. It stops after repeated idle rounds or after 80 scrolls. The local server then classifies unclassified rows with rules and exports the HTML archive.

GraphQL export readiness:

1. Reload the extension after changing permissions.
2. Open or refresh `https://x.com/i/bookmarks` while logged in.
3. Open the popup and check X Session Capture.

Expected ready state:

- User captured.
- Auth captured.
- Bookmarks queryId captured.

This phase only captures local readiness state. It does not start GraphQL export requests.

GraphQL export:

1. Confirm User, Auth, and Bookmarks queryId are captured.
2. Confirm the local UI is running at `http://127.0.0.1:8765`.
3. Click GraphQL Export.

The background service worker pages through X.com's internal bookmarks GraphQL endpoint with the captured browser session. Each page is imported into the local API. At the end, the local server classifies unclassified rows and exports HTML to `archive/`.
