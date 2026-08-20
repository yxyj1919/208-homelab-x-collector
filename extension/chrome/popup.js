const DEFAULT_API_BASE_URL = "http://127.0.0.1:8765";

const apiUrlElement = document.querySelector("#api-url");
const statusElement = document.querySelector("#status");
const pageKindElement = document.querySelector("#page-kind");
const tabTitleElement = document.querySelector("#tab-title");
const tabUrlElement = document.querySelector("#tab-url");
const graphqlExportButton = document.querySelector("#graphql-export");
const graphqlDownloadButton = document.querySelector("#graphql-download");
const optionsButton = document.querySelector("#options");
const graphqlStatusElement = document.querySelector("#graphql-status");
const userDot = document.querySelector("#user-dot");
const userStatus = document.querySelector("#user-status");
const authDot = document.querySelector("#auth-dot");
const authStatus = document.querySelector("#auth-status");
const queryDot = document.querySelector("#query-dot");
const queryStatus = document.querySelector("#query-status");
const sessionHint = document.querySelector("#session-hint");

let apiBaseUrl = DEFAULT_API_BASE_URL;
let activeTabId = null;
let activePageKind = "other";
let graphqlReady = false;
let graphqlProgressTimer = null;

document.addEventListener("DOMContentLoaded", async () => {
  apiBaseUrl = await getApiBaseUrl();
  apiUrlElement.textContent = apiBaseUrl;
  graphqlExportButton.addEventListener("click", startGraphqlExport);
  graphqlDownloadButton.addEventListener("click", startGraphqlDownload);
  optionsButton.addEventListener("click", () => chrome.runtime.openOptionsPage());

  await renderActiveTab();
  await checkLocalService();
  await renderXSessionStatus();
  await renderGraphqlProgress();
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

function setActionButtonsDisabled(disabled) {
  graphqlExportButton.disabled = disabled || !graphqlReady;
  graphqlDownloadButton.disabled = disabled || !graphqlReady;
}

async function startGraphqlExport() {
  setActionButtonsDisabled(true);
  graphqlStatusElement.textContent = "Starting GraphQL export...";
  graphqlStatusElement.classList.remove("error");

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
    graphqlStatusElement.textContent = response.started
      ? "GraphQL export started. You can switch tabs; completion will be notified."
      : `GraphQL ${response.action || "export"} is already running.`;
    startGraphqlProgressPolling();
  } catch (error) {
    graphqlStatusElement.textContent = error.message || String(error);
    graphqlStatusElement.classList.add("error");
    setActionButtonsDisabled(false);
  }
}

async function startGraphqlDownload() {
  setActionButtonsDisabled(true);
  graphqlStatusElement.textContent = "Starting GraphQL download...";
  graphqlStatusElement.classList.remove("error");

  try {
    const response = await chrome.runtime.sendMessage({
      type: "xbookmarks.graphqlDownload",
      apiBaseUrl,
      pageSize: 100,
      maxPages: 200,
    });
    if (!response?.ok) {
      throw new Error(response?.error || "GraphQL download failed");
    }
    graphqlStatusElement.textContent = response.started
      ? "GraphQL download started. You can switch tabs; completion will be notified."
      : `GraphQL ${response.action || "download"} is already running.`;
    startGraphqlProgressPolling();
  } catch (error) {
    graphqlStatusElement.textContent = error.message || String(error);
    graphqlStatusElement.classList.add("error");
    setActionButtonsDisabled(false);
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
  if (!progress) {
    return;
  }
  const action = progress.action === "download" ? "download" : "export";
  if (progress.state === "complete") {
    stopGraphqlProgressPolling();
    graphqlStatusElement.classList.remove("error");
    graphqlStatusElement.textContent = action === "download"
      ? `Download complete: ${progress.total || 0} bookmark(s); pages=${progress.pages || 0}; file=${progress.filename || "JSON file"}.`
      : `Export complete: ${progress.total || 0} bookmark(s); pages=${progress.pages || 0}; ` +
        `new=${progress.inserted || 0} updated=${progress.updated || 0} unchanged=${progress.unchanged || 0}; ` +
        `classified=${progress.classified || 0} html=${progress.exported || 0}.`;
    setActionButtonsDisabled(false);
    await checkLocalService();
    return;
  }
  if (progress.state === "failed") {
    stopGraphqlProgressPolling();
    graphqlStatusElement.textContent = progress.error || `GraphQL ${action} failed.`;
    graphqlStatusElement.classList.add("error");
    setActionButtonsDisabled(false);
    return;
  }
  if (progress.state !== "running") {
    return;
  }
  setActionButtonsDisabled(true);
  graphqlStatusElement.textContent =
    `GraphQL ${action} running: pages=${progress.pages || 0} fetched=${progress.total || 0}.`;
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
    sessionHint.textContent = "Ready to export or download bookmarks.";
    sessionHint.classList.remove("error");
    graphqlExportButton.disabled = false;
    graphqlDownloadButton.disabled = false;
  } else {
    sessionHint.textContent = "Open or refresh https://x.com/i/bookmarks while logged in.";
    sessionHint.classList.add("error");
    graphqlExportButton.disabled = true;
    graphqlDownloadButton.disabled = true;
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
