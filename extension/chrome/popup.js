const DEFAULT_API_BASE_URL = "http://127.0.0.1:8765";

const apiUrlElement = document.querySelector("#api-url");
const statusElement = document.querySelector("#status");
const pageKindElement = document.querySelector("#page-kind");
const tabTitleElement = document.querySelector("#tab-title");
const tabUrlElement = document.querySelector("#tab-url");
const openUiButton = document.querySelector("#open-ui");
const graphqlExportButton = document.querySelector("#graphql-export");
const captureButton = document.querySelector("#capture");
const captureAllButton = document.querySelector("#capture-all");
const optionsButton = document.querySelector("#options");
const captureStatusElement = document.querySelector("#capture-status");
const graphqlStatusElement = document.querySelector("#graphql-status");
const userDot = document.querySelector("#user-dot");
const userStatus = document.querySelector("#user-status");
const authDot = document.querySelector("#auth-dot");
const authStatus = document.querySelector("#auth-status");
const queryDot = document.querySelector("#query-dot");
const queryStatus = document.querySelector("#query-status");
const captureHint = document.querySelector("#capture-hint");

let apiBaseUrl = DEFAULT_API_BASE_URL;
let activeTabId = null;
let activePageKind = "other";
let graphqlReady = false;
let graphqlProgressTimer = null;

document.addEventListener("DOMContentLoaded", async () => {
  apiBaseUrl = await getApiBaseUrl();
  apiUrlElement.textContent = apiBaseUrl;
  openUiButton.addEventListener("click", () => chrome.tabs.create({ url: apiBaseUrl }));
  graphqlExportButton.addEventListener("click", startGraphqlExport);
  captureButton.addEventListener("click", captureVisibleBookmarks);
  captureAllButton.addEventListener("click", captureAllBookmarks);
  optionsButton.addEventListener("click", () => chrome.runtime.openOptionsPage());

  await renderActiveTab();
  await checkLocalService();
  await renderXSessionStatus();
});

async function getApiBaseUrl() {
  const result = await chrome.storage.local.get({ apiBaseUrl: DEFAULT_API_BASE_URL });
  return normalizeBaseUrl(result.apiBaseUrl);
}

async function renderActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    return;
  }
  activeTabId = tab.id;

  tabTitleElement.textContent = tab.title || "Untitled";
  tabTitleElement.classList.remove("muted");
  tabUrlElement.textContent = tab.url || "";

  const pageKind = classifyPage(tab.url || "");
  activePageKind = pageKind.kind;
  pageKindElement.textContent = pageKind.label;
  pageKindElement.classList.toggle("good", pageKind.kind !== "other");
  captureButton.disabled = !["bookmarks", "x"].includes(activePageKind);
  captureAllButton.disabled = activePageKind !== "bookmarks";
}

async function checkLocalService() {
  try {
    const response = await fetch(`${apiBaseUrl}/api/sync-status`, { method: "GET" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    statusElement.textContent = "Local service is reachable.";
    statusElement.classList.remove("error");
  } catch (error) {
    statusElement.textContent = "Local service is not reachable. Start: xbookmarks web";
    statusElement.classList.add("error");
  }
}

async function captureVisibleBookmarks() {
  if (!activeTabId || !["bookmarks", "x"].includes(activePageKind)) {
    captureStatusElement.textContent = "Open an X bookmarks page first.";
    captureStatusElement.classList.add("error");
    return;
  }

  setCaptureDisabled(true);
  captureStatusElement.textContent = "Capturing visible bookmarks...";
  captureStatusElement.classList.remove("error");

  try {
    const response = await chrome.tabs.sendMessage(activeTabId, {
      type: "xbookmarks.captureVisible",
    });
    await importCapturedItems(response, "No visible tweets found. Scroll the bookmarks page and retry.");
    await checkLocalService();
  } catch (error) {
    captureStatusElement.textContent = error.message || String(error);
    captureStatusElement.classList.add("error");
  } finally {
    setCaptureDisabled(false);
  }
}

async function captureAllBookmarks() {
  if (!activeTabId || activePageKind !== "bookmarks") {
    captureStatusElement.textContent = "Open https://x.com/i/bookmarks first.";
    captureStatusElement.classList.add("error");
    return;
  }

  setCaptureDisabled(true);
  captureStatusElement.textContent = "Auto scrolling and capturing bookmarks...";
  captureStatusElement.classList.remove("error");

  try {
    const response = await chrome.tabs.sendMessage(activeTabId, {
      type: "xbookmarks.captureAll",
      maxScrolls: 80,
      idleRounds: 5,
      delayMs: 900,
    });
    await importCapturedItems(response, "No bookmarks were captured from the page.");
    await checkLocalService();
  } catch (error) {
    captureStatusElement.textContent = error.message || String(error);
    captureStatusElement.classList.add("error");
  } finally {
    setCaptureDisabled(false);
  }
}

async function importCapturedItems(response, emptyMessage) {
  const items = response?.items || [];
  if (!items.length) {
    throw new Error(emptyMessage);
  }

  const importResponse = await fetch(`${apiBaseUrl}/api/extension/bookmarks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source: "chrome-extension",
      source_url: response.source_url,
      captured_at: new Date().toISOString(),
      classify: true,
      export_html: true,
      archive_dir: "archive",
      items,
    }),
  });
  const body = await importResponse.json();
  if (!importResponse.ok) {
    throw new Error(body.error || `HTTP ${importResponse.status}`);
  }
  captureStatusElement.textContent =
    `Captured ${body.unique_seen}; inserted=${body.inserted} updated=${body.updated} ` +
    `unchanged=${body.unchanged} classified=${body.classified} exported=${body.exported}.`;
}

function setCaptureDisabled(disabled) {
  graphqlExportButton.disabled = disabled || !graphqlReady;
  captureButton.disabled = disabled || !["bookmarks", "x"].includes(activePageKind);
  captureAllButton.disabled = disabled || activePageKind !== "bookmarks";
}

async function startGraphqlExport() {
  setCaptureDisabled(true);
  graphqlStatusElement.textContent = "Starting GraphQL export...";
  graphqlStatusElement.classList.remove("error");
  startGraphqlProgressPolling();

  try {
    const response = await chrome.runtime.sendMessage({
      type: "xbookmarks.graphqlExport",
      apiBaseUrl,
      archiveDir: "archive",
      pageSize: 100,
      maxPages: 200,
    });
    if (!response?.ok) {
      throw new Error(response?.error || "GraphQL export failed");
    }
    graphqlStatusElement.textContent =
      `GraphQL exported ${response.total}; pages=${response.pages} ` +
      `inserted=${response.inserted} updated=${response.updated} unchanged=${response.unchanged} ` +
      `classified=${response.classified} html=${response.exported}.`;
    await checkLocalService();
  } catch (error) {
    graphqlStatusElement.textContent = error.message || String(error);
    graphqlStatusElement.classList.add("error");
  } finally {
    stopGraphqlProgressPolling();
    setCaptureDisabled(false);
    await renderXSessionStatus();
  }
}

function startGraphqlProgressPolling() {
  stopGraphqlProgressPolling();
  graphqlProgressTimer = setInterval(renderGraphqlProgress, 1000);
  renderGraphqlProgress();
}

function stopGraphqlProgressPolling() {
  if (graphqlProgressTimer) {
    clearInterval(graphqlProgressTimer);
    graphqlProgressTimer = null;
  }
}

async function renderGraphqlProgress() {
  const state = await chrome.storage.local.get("xbookmarks_graphql_export_status");
  const progress = state.xbookmarks_graphql_export_status;
  if (!progress || progress.state !== "running") {
    return;
  }
  graphqlStatusElement.textContent =
    `GraphQL running: pages=${progress.pages || 0} imported=${progress.total || 0} ` +
    `inserted=${progress.inserted || 0} updated=${progress.updated || 0} ` +
    `unchanged=${progress.unchanged || 0} classified=${progress.classified || 0}.`;
}

async function renderXSessionStatus() {
  const state = await chrome.storage.local.get(null);
  const userId = state.xbookmarks_user_id || "";
  const screenName = state.xbookmarks_screen_name || "";
  const creds = userId ? state[`xbookmarks_x_creds_${userId}`] : state.xbookmarks_x_creds_unknown;
  const hasAuth = Boolean(creds?.authorization || creds?.["x-csrf-token"]);
  const hasBookmarksQuery = Boolean(creds?.queryId_Bookmarks);
  graphqlReady = Boolean(userId && hasAuth && hasBookmarksQuery);

  setStatus(userDot, userStatus, Boolean(userId), userId ? userLabel(userId, screenName) : "User: not captured");
  setStatus(authDot, authStatus, hasAuth, hasAuth ? `Auth: captured ${ageLabel(creds.captured_at)}` : "Auth: not captured");
  setStatus(
    queryDot,
    queryStatus,
    hasBookmarksQuery,
    hasBookmarksQuery ? "Bookmarks queryId: captured" : "Bookmarks queryId: not captured",
  );

  if (graphqlReady) {
    captureHint.textContent = "Ready for GraphQL export phase.";
    captureHint.classList.remove("error");
    graphqlExportButton.disabled = false;
  } else {
    captureHint.textContent = "Open or refresh https://x.com/i/bookmarks while logged in.";
    captureHint.classList.add("error");
    graphqlExportButton.disabled = true;
  }
}

function setStatus(dot, textElement, good, text) {
  dot.classList.toggle("good", good);
  dot.classList.toggle("error", !good);
  textElement.textContent = text;
}

function userLabel(userId, screenName) {
  return screenName ? `User: @${screenName} (${userId})` : `User: ${userId}`;
}

function ageLabel(timestamp) {
  if (!timestamp) {
    return "";
  }
  const minutes = Math.max(0, Math.round((Date.now() - Number(timestamp)) / 60000));
  return `(${minutes} min ago)`;
}

function classifyPage(url) {
  try {
    const parsed = new URL(url);
    if (!["x.com", "twitter.com"].includes(parsed.hostname)) {
      return { kind: "other", label: "Other page" };
    }
    if (parsed.pathname === "/i/bookmarks") {
      return { kind: "bookmarks", label: "Bookmarks page" };
    }
    if (/\/status\/\d+/.test(parsed.pathname)) {
      return { kind: "tweet", label: "Tweet page" };
    }
    return { kind: "x", label: "X page" };
  } catch {
    return { kind: "other", label: "Other page" };
  }
}

function normalizeBaseUrl(value) {
  const raw = String(value || DEFAULT_API_BASE_URL).trim();
  return raw.replace(/\/+$/, "") || DEFAULT_API_BASE_URL;
}
