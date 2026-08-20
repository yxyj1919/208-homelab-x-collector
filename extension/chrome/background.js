const GRAPHQL_PATTERN = /\/graphql\/([^/]+)\/(Bookmarks|BookmarkFoldersSlice|BookmarkFolderTimeline)\b/;
const PUBLIC_X_BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA";
const BASE_DELAY_MS = 2500;
const MAX_RETRIES = 5;
const MAX_EMPTY_PAGES = 3;
let graphqlJob = null;
const CAPTURE_HEADERS = new Set([
  "authorization",
  "x-csrf-token",
  "x-client-uuid",
  "x-client-transaction-id",
]);

chrome.webRequest.onSendHeaders.addListener(
  (details) => {
    const captured = {};
    for (const header of details.requestHeaders || []) {
      const name = header.name.toLowerCase();
      if (CAPTURE_HEADERS.has(name) && header.value) {
        captured[name] = header.value;
      }
    }

    const url = new URL(details.url);
    const graphqlMatch = url.pathname.match(GRAPHQL_PATTERN);
    if (graphqlMatch) {
      const [, queryId, operation] = graphqlMatch;
      captured[`queryId_${operation}`] = queryId;
    }

    const features = url.searchParams.get("features");
    if (features) {
      captured.captured_features = features;
    }

    if (!Object.keys(captured).length) {
      return;
    }

    chrome.storage.local.get(["xbookmarks_user_id"], (state) => {
      const userId = state.xbookmarks_user_id || "unknown";
      const storageKey = `xbookmarks_x_creds_${userId}`;
      chrome.storage.local.get([storageKey], (existing) => {
        const merged = {
          ...(existing[storageKey] || {}),
          ...captured,
          captured_at: Date.now(),
        };
        chrome.storage.local.set({ [storageKey]: merged });
      });
    });
  },
  { urls: ["https://x.com/i/api/graphql/*", "https://twitter.com/i/api/graphql/*"], types: ["xmlhttprequest"] },
  ["requestHeaders", "extraHeaders"],
);

chrome.runtime.onInstalled.addListener(registerGraphqlHeaderRule);
registerGraphqlHeaderRule();

function registerGraphqlHeaderRule() {
  chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: [1],
    addRules: [
      {
        id: 1,
        priority: 1,
        action: {
          type: "modifyHeaders",
          requestHeaders: [
            { header: "Origin", operation: "set", value: "https://x.com" },
            { header: "Referer", operation: "set", value: "https://x.com/" },
          ],
        },
        condition: {
          urlFilter: "https://x.com/i/api/graphql/*",
          resourceTypes: ["xmlhttprequest"],
          initiatorDomains: [chrome.runtime.id],
        },
      },
    ],
  });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!["xbookmarks.graphqlExport", "xbookmarks.graphqlDownload"].includes(message?.type)) {
    return false;
  }
  startGraphqlJob(message)
    .then((result) => sendResponse({ ok: true, ...result }))
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

async function startGraphqlJob(message) {
  if (graphqlJob) {
    return { started: false, running: true, action: graphqlJob.action };
  }
  const progressState = await chromeStorageGet("xbookmarks_graphql_export_status");
  const progress = progressState.xbookmarks_graphql_export_status;
  if (progress?.state === "running" && Date.now() - Number(progress.updated_at || 0) < 30 * 60 * 1000) {
    return { started: false, running: true, action: progress.action || "export" };
  }

  const action = message.type === "xbookmarks.graphqlDownload" ? "download" : "export";
  const jobId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  graphqlJob = { action, jobId };
  await saveGraphqlProgress("running", { action, job_id: jobId, pages: 0, total: 0 });
  await reportExtensionProgress(message.apiBaseUrl, {
    state: "running",
    action,
    job_id: jobId,
    pages: 0,
    total: 0,
  });

  const runner = action === "download" ? runGraphqlDownload : runGraphqlExport;
  runner({ ...message, action, jobId })
    .then(async (result) => {
      await saveGraphqlProgress("complete", { action, job_id: jobId, ...result });
      await reportExtensionProgress(message.apiBaseUrl, {
        state: "complete",
        action,
        job_id: jobId,
        ...result,
      });
      await showGraphqlNotification(action, result);
    })
    .catch(async (error) => {
      const messageText = error.message || String(error);
      await saveGraphqlProgress("failed", { action, job_id: jobId, error: messageText });
      await reportExtensionProgress(message.apiBaseUrl, {
        state: "failed",
        action,
        job_id: jobId,
        error: messageText,
      });
      await showGraphqlNotification(action, { error: messageText });
    })
    .finally(() => {
      if (graphqlJob?.jobId === jobId) {
        graphqlJob = null;
      }
    });

  return { started: true, running: true, action, job_id: jobId };
}

async function runGraphqlExport(options) {
  const { creds } = await graphqlExportState();
  const apiBaseUrl = normalizeBaseUrl(options.apiBaseUrl || "http://127.0.0.1:8765");
  const pageSize = clampNumber(options.pageSize, 1, 100, 100);
  const maxPages = clampNumber(options.maxPages, 1, 500, 200);
  let pages = 0;
  let total = 0;
  const bookmarks = [];

  const result = await pageGraphqlBookmarks({
    creds,
    pageSize,
    maxPages,
    onPage: async ({ tweets, pages: currentPages, total: currentTotal }) => {
      pages = currentPages;
      total = currentTotal;
      bookmarks.push(...tweets);
      await saveGraphqlProgress("running", {
        action: options.action,
        job_id: options.jobId,
        pages,
        total,
      });
      await reportExtensionProgress(apiBaseUrl, {
        state: "running",
        action: options.action,
        job_id: options.jobId,
        pages,
        total,
      });
    },
  });
  pages = result.pages;
  total = result.total;

  const imported = await postBookmarks(apiBaseUrl, {
    source: "chrome-extension-graphql",
    source_url: "https://x.com/i/bookmarks",
    captured_at: new Date().toISOString(),
    classify: true,
    export_html: true,
    archive_dir: options.archiveDir || "archive",
    summary: {
      source: total,
      unique: total,
      imported: total,
    },
    items: bookmarks,
  });

  return {
    pages,
    total,
    inserted: Number(imported.inserted || 0),
    updated: Number(imported.updated || 0),
    unchanged: Number(imported.unchanged || 0),
    duplicates: Number(imported.duplicates || 0),
    classified: Number(imported.classified || 0),
    exported: Number(imported.exported || 0),
  };
}

async function runGraphqlDownload(options) {
  const { userId, screenName, creds } = await graphqlExportState();
  const apiBaseUrl = normalizeBaseUrl(options.apiBaseUrl || "http://127.0.0.1:8765");
  const pageSize = clampNumber(options.pageSize, 1, 100, 100);
  const maxPages = clampNumber(options.maxPages, 1, 500, 200);
  const bookmarks = [];
  const result = await pageGraphqlBookmarks({
    creds,
    pageSize,
    maxPages,
    onPage: async ({ tweets, pages, total }) => {
      bookmarks.push(...tweets);
      await saveGraphqlProgress("running", {
        action: options.action,
        job_id: options.jobId,
        pages,
        total,
      });
      await reportExtensionProgress(apiBaseUrl, {
        state: "running",
        action: options.action,
        job_id: options.jobId,
        pages,
        total,
      });
    },
  });
  const filename = await downloadBookmarksJson({
    bookmarks,
    pages: result.pages,
    userId,
    screenName,
  });
  const downloadResult = { pages: result.pages, total: result.total, filename };
  return downloadResult;
}

async function graphqlExportState() {
  const state = await chromeStorageGet(null);
  const userId = state.xbookmarks_user_id;
  if (!userId) {
    throw new Error("User is not captured. Open https://x.com/i/bookmarks first.");
  }
  const creds = state[`xbookmarks_x_creds_${userId}`];
  if (!creds?.queryId_Bookmarks) {
    throw new Error("Bookmarks queryId is not captured. Refresh https://x.com/i/bookmarks.");
  }
  return { userId, screenName: state.xbookmarks_screen_name || "", creds };
}

async function pageGraphqlBookmarks({ creds, pageSize, maxPages, onPage }) {
  let cursor = null;
  let pages = 0;
  let total = 0;
  let emptyPages = 0;

  while (pages < maxPages) {
    const response = await fetchBookmarksPage({
      queryId: creds.queryId_Bookmarks,
      creds,
      cursor,
      count: pageSize,
    });
    const { tweets, bottomCursor } = parseBookmarksPage(response.data);
    pages += 1;

    if (tweets.length) {
      emptyPages = 0;
      total += tweets.length;
    } else {
      emptyPages += 1;
    }

    await onPage({ tweets, pages, total });

    if (!bottomCursor || bottomCursor === cursor || emptyPages >= MAX_EMPTY_PAGES) {
      break;
    }
    cursor = bottomCursor;
    await sleep(jitteredDelay());
  }
  return { pages, total };
}

async function saveGraphqlProgress(state, progress) {
  await chrome.storage.local.set({
    xbookmarks_graphql_export_status: {
      state,
      ...progress,
      updated_at: Date.now(),
    },
  });
}

async function fetchBookmarksPage({ queryId, creds, cursor, count }) {
  const variables = { count };
  if (cursor) {
    variables.cursor = cursor;
  }

  for (let attempt = 0; attempt < MAX_RETRIES; attempt += 1) {
    const data = await graphqlRequest(queryId, "Bookmarks", variables, creds);
    if (!data.error) {
      return data;
    }
    if (data.error === "auth_error") {
      throw new Error(`X auth failed: HTTP ${data.status}`);
    }
    const delayMs = data.error === "rate_limited"
      ? BASE_DELAY_MS * Math.pow(2, attempt + 1)
      : 1000 * Math.pow(2, attempt + 1);
    await sleep(delayMs);
  }
  throw new Error("X GraphQL request failed after retries");
}

async function graphqlRequest(queryId, operationName, variables, creds) {
  const params = new URLSearchParams();
  params.set("variables", JSON.stringify(variables));
  params.set("features", featuresJson(creds));
  const url = `https://x.com/i/api/graphql/${queryId}/${operationName}?${params.toString()}`;
  const response = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: await buildGraphqlHeaders(creds),
  });

  if (response.status === 429) {
    return { error: "rate_limited", status: 429, data: null };
  }
  if (response.status === 401 || response.status === 403) {
    return { error: "auth_error", status: response.status, data: null };
  }
  if (!response.ok) {
    return { error: "http_error", status: response.status, data: null };
  }

  const data = await response.json();
  const rateLimitError = Array.isArray(data.errors)
    ? data.errors.find((item) => item?.code === 88)
    : null;
  if (rateLimitError) {
    return { error: "rate_limited", status: response.status, data: null };
  }
  if (data.errors) {
    return { error: "graphql_error", status: response.status, data: null };
  }
  return { error: null, status: response.status, data };
}

async function buildGraphqlHeaders(creds) {
  const csrfToken = await freshCt0() || creds?.["x-csrf-token"] || "";
  const transactionId = incrementTransactionId(creds?.["x-client-transaction-id"]);
  const headers = {
    "Authorization": creds?.authorization || `Bearer ${PUBLIC_X_BEARER_TOKEN}`,
    "X-Csrf-Token": csrfToken,
    "X-Twitter-Active-User": "yes",
    "X-Twitter-Auth-Type": "OAuth2Session",
    "X-Twitter-Client-Language": "en",
    "Content-Type": "application/json",
  };
  if (creds?.["x-client-uuid"]) {
    headers["X-Client-Uuid"] = creds["x-client-uuid"];
  }
  if (transactionId) {
    headers["X-Client-Transaction-Id"] = transactionId;
    creds["x-client-transaction-id"] = transactionId;
  }
  return headers;
}

function parseBookmarksPage(data) {
  const instructions =
    data?.data?.bookmark_timeline_v2?.timeline?.instructions ||
    data?.data?.bookmark_timeline?.timeline?.instructions ||
    [];
  const tweets = [];
  let bottomCursor = null;
  for (const instruction of instructions) {
    if (instruction?.type !== "TimelineAddEntries" || !Array.isArray(instruction.entries)) {
      continue;
    }
    for (const entry of instruction.entries) {
      const entryId = entry.entryId || "";
      if (entryId.startsWith("tweet-")) {
        const tweet = extractTweet(entry);
        if (tweet) {
          tweets.push(tweet);
        }
      }
      if (entryId.startsWith("cursor-bottom-")) {
        bottomCursor = entry.content?.value || null;
      }
    }
  }
  return { tweets, bottomCursor };
}

function extractTweet(entry) {
  let result = entry.content?.itemContent?.tweet_results?.result;
  if (!result) {
    return null;
  }
  if (result.__typename === "TweetWithVisibilityResults") {
    result = result.tweet;
  }
  if (!result || result.__typename === "TweetTombstone") {
    return null;
  }

  const legacy = result.legacy || {};
  const user = result.core?.user_results?.result;
  const userLegacy = user?.legacy || {};
  const tweetId = result.rest_id || legacy.id_str;
  const text = result.note_tweet?.note_tweet_results?.result?.text || legacy.full_text || "";
  if (!tweetId || !text) {
    return null;
  }

  const author = {
    user_id: user?.rest_id || userLegacy.id_str || null,
    screen_name: userLegacy.screen_name || null,
    name: userLegacy.name || null,
    profile_image_url: userLegacy.profile_image_url_https || null,
    verified: Boolean(userLegacy.verified),
    followers_count: Number(userLegacy.followers_count || 0),
  };
  return {
    tweet_id: tweetId,
    url: `https://x.com/i/status/${tweetId}`,
    text,
    full_text: text,
    author,
    created_at: legacy.created_at || null,
    status: "available",
    sort_index: entry.sortIndex || null,
    metrics: {
      likes: Number(legacy.favorite_count || 0),
      retweets: Number(legacy.retweet_count || 0),
      replies: Number(legacy.reply_count || 0),
      bookmarks: Number(legacy.bookmark_count || 0),
      views: Number(result.views?.count || 0),
    },
    media: extractMedia(legacy),
    quoted_tweet: extractQuotedTweet(result),
    card: extractCard(result),
  };
}

function extractMedia(legacy) {
  const media = legacy.extended_entities?.media || legacy.entities?.media || [];
  return media.map((item) => ({
    type: item.type || "media",
    url: item.type === "photo" ? `${item.media_url_https}?format=jpg&name=orig` : item.media_url_https,
    alt_text: item.ext_alt_text || null,
  })).filter((item) => item.url);
}

function extractQuotedTweet(result) {
  let quoted = result.quoted_status_result?.result;
  if (!quoted) {
    return null;
  }
  if (quoted.__typename === "TweetWithVisibilityResults") {
    quoted = quoted.tweet;
  }
  if (!quoted || quoted.__typename === "TweetTombstone") {
    return { status: "unavailable" };
  }
  const legacy = quoted.legacy || {};
  const userLegacy = quoted.core?.user_results?.result?.legacy || {};
  const tweetId = quoted.rest_id || legacy.id_str || null;
  return {
    tweet_id: tweetId,
    full_text: quoted.note_tweet?.note_tweet_results?.result?.text || legacy.full_text || null,
    author: {
      screen_name: userLegacy.screen_name || null,
      name: userLegacy.name || null,
    },
    url: tweetId ? `https://x.com/i/status/${tweetId}` : null,
  };
}

function extractCard(result) {
  const legacy = result.card?.legacy;
  if (!legacy) {
    return null;
  }
  const bindings = {};
  for (const item of legacy.binding_values || []) {
    bindings[item.key] = item.value?.string_value || item.value?.scribe_value?.description || null;
  }
  return {
    type: legacy.name || null,
    url: bindings.card_url || bindings.url || null,
    title: bindings.title || null,
    description: bindings.description || null,
  };
}

async function postBookmarks(apiBaseUrl, payload) {
  const response = await fetch(`${apiBaseUrl}/api/extension/bookmarks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error || `Local import failed: HTTP ${response.status}`);
  }
  return body;
}

async function reportExtensionProgress(apiBaseUrl, payload) {
  const baseUrl = normalizeBaseUrl(apiBaseUrl || "http://127.0.0.1:8765");
  try {
    await fetch(`${baseUrl}/api/extension/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, updated_at: Date.now() }),
    });
  } catch {}
}

async function downloadBookmarksJson({ bookmarks, pages, userId, screenName }) {
  const exportedAt = new Date().toISOString();
  const safeUser = (screenName || userId || "x-user").replace(/[^a-zA-Z0-9_-]+/g, "-");
  const timestamp = exportedAt.replace(/[:.]/g, "-");
  const filename = `xbookmarks-${safeUser}-${timestamp}.json`;
  const payload = {
    export_metadata: {
      source: "chrome-extension-graphql",
      source_url: "https://x.com/i/bookmarks",
      exported_at: exportedAt,
      pages,
      count: bookmarks.length,
      user_id: userId,
      screen_name: screenName || null,
    },
    folders: [],
    bookmarks,
  };
  const dataUrl = `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(payload, null, 2))}`;
  await chromeDownloadsDownload({
    url: dataUrl,
    filename,
    saveAs: true,
  });
  return filename;
}

async function freshCt0() {
  const cookie = await chromeCookieGet({ url: "https://x.com", name: "ct0" });
  return cookie?.value || null;
}

function featuresJson(creds) {
  if (creds?.captured_features) {
    try {
      JSON.parse(decodeURIComponent(creds.captured_features));
      return decodeURIComponent(creds.captured_features);
    } catch {
      return creds.captured_features;
    }
  }
  return JSON.stringify({
    graphql_timeline_v2_bookmark_timeline: true,
    responsive_web_graphql_exclude_directive_enabled: true,
    responsive_web_graphql_timeline_navigation_enabled: true,
    responsive_web_graphql_skip_user_profile_image_extensions_enabled: false,
    responsive_web_twitter_article_tweet_consumption_enabled: true,
    tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled: true,
    longform_notetweets_rich_text_read_enabled: true,
    longform_notetweets_inline_media_enabled: true,
  });
}

function incrementTransactionId(transactionId) {
  if (!transactionId) {
    return null;
  }
  const chars = transactionId.split("");
  const positions = [];
  for (let index = 0; index < chars.length; index += 1) {
    if (/\d/.test(chars[index])) {
      positions.push(index);
    }
  }
  if (!positions.length) {
    return transactionId;
  }
  const position = positions[Math.floor(Math.random() * positions.length)];
  chars[position] = String((Number(chars[position]) + 1) % 10);
  return chars.join("");
}

function chromeStorageGet(keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

function chromeCookieGet(details) {
  return new Promise((resolve) => chrome.cookies.get(details, resolve));
}

function chromeDownloadsDownload(details) {
  return new Promise((resolve, reject) => {
    chrome.downloads.download(details, (downloadId) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(downloadId);
    });
  });
}

async function showGraphqlNotification(action, result) {
  const failed = Boolean(result.error);
  const title = failed
    ? "X Bookmarks export failed"
    : action === "download"
      ? "X Bookmarks download complete"
      : "X Bookmarks export complete";
  const message = failed
    ? result.error
    : action === "download"
      ? `Downloaded ${result.total || 0} bookmark(s) to ${result.filename || "JSON file"}.`
      : `Exported ${result.total || 0} bookmark(s); updated=${result.updated || 0}, new=${result.inserted || 0}.`;
  try {
    await chromeNotificationsCreate({
      type: "basic",
      iconUrl: "icon-128.svg",
      title,
      message,
    });
  } catch {}
}

function chromeNotificationsCreate(details) {
  return new Promise((resolve, reject) => {
    chrome.notifications.create(`xbookmarks-${Date.now()}`, details, (notificationId) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(notificationId);
    });
  });
}

function clampNumber(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.min(Math.max(Math.floor(number), min), max);
}

function jitteredDelay() {
  return BASE_DELAY_MS * (0.7 + Math.random() * 0.8);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeBaseUrl(value) {
  const raw = String(value || "http://127.0.0.1:8765").trim();
  return raw.replace(/\/+$/, "") || "http://127.0.0.1:8765";
}
