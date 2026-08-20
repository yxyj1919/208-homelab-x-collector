(async function initXBookmarksHelper() {
  if (window.__xBookmarksHelperLoaded) {
    return;
  }
  window.__xBookmarksHelperLoaded = true;
  captureXIdentity();
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "xbookmarks.captureVisible") {
      sendResponse({
        source_url: window.location.href,
        items: captureVisibleTweets(),
      });
      return false;
    }
    if (message?.type === "xbookmarks.captureAll") {
      captureAllTweets(message).then(sendResponse);
      return true;
    }
    return false;
  });
})();

function captureXIdentity() {
  const userId = document.cookie.match(/(?:^|;\s*)twid=u%3D(\d+)/)?.[1];
  if (userId) {
    chrome.storage.local.set({ xbookmarks_user_id: userId });
  }

  const tryScreenName = () => {
    const profileLink = document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
    const href = profileLink?.getAttribute("href") || "";
    if (!href.startsWith("/")) {
      return false;
    }
    const screenName = href.slice(1).split("/")[0];
    if (!screenName || screenName.includes("?")) {
      return false;
    }
    chrome.storage.local.set({ xbookmarks_screen_name: screenName });
    return true;
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => pollScreenName(tryScreenName));
  } else {
    pollScreenName(tryScreenName);
  }
}

function pollScreenName(capture) {
  if (capture()) {
    return;
  }
  let attempts = 0;
  const interval = setInterval(() => {
    attempts += 1;
    if (capture() || attempts >= 10) {
      clearInterval(interval);
    }
  }, 1000);
}

function captureVisibleTweets() {
  const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
  const byId = new Map();
  for (const article of articles) {
    const bookmark = articleToBookmark(article);
    if (bookmark) {
      byId.set(bookmark.tweet_id, bookmark);
    }
  }
  return Array.from(byId.values());
}

async function captureAllTweets(options = {}) {
  const maxScrolls = Number(options.maxScrolls || 80);
  const idleRoundsLimit = Number(options.idleRounds || 5);
  const delayMs = Number(options.delayMs || 900);
  const byId = new Map();
  let idleRounds = 0;
  let previousSize = 0;

  window.scrollTo({ top: 0, behavior: "auto" });
  await delay(delayMs);

  for (let index = 0; index < maxScrolls; index += 1) {
    for (const bookmark of captureVisibleTweets()) {
      byId.set(bookmark.tweet_id, bookmark);
    }

    if (byId.size === previousSize) {
      idleRounds += 1;
    } else {
      idleRounds = 0;
      previousSize = byId.size;
    }

    const beforeY = window.scrollY;
    window.scrollBy({ top: Math.max(window.innerHeight * 0.85, 650), behavior: "smooth" });
    await delay(delayMs);
    const atPageEnd =
      Math.ceil(window.scrollY + window.innerHeight) >= document.documentElement.scrollHeight;
    if ((atPageEnd && window.scrollY === beforeY) || idleRounds >= idleRoundsLimit) {
      break;
    }
  }

  for (const bookmark of captureVisibleTweets()) {
    byId.set(bookmark.tweet_id, bookmark);
  }

  return {
    source_url: window.location.href,
    items: Array.from(byId.values()),
  };
}

function articleToBookmark(article) {
  const statusLink = Array.from(article.querySelectorAll('a[href*="/status/"]'))
    .map((link) => link.getAttribute("href") || "")
    .find((href) => /\/status\/\d+/.test(href));
  const tweetId = statusLink?.match(/\/status\/(\d+)/)?.[1];
  if (!tweetId) {
    return null;
  }

  const tweetText = article.querySelector('[data-testid="tweetText"]');
  const text = normalizeText(tweetText?.innerText || article.innerText || "");
  if (!text) {
    return null;
  }

  const author = extractAuthor(article, statusLink);
  const timeElement = article.querySelector("time");
  const createdAt = timeElement?.getAttribute("datetime") || null;
  return {
    tweet_id: tweetId,
    url: absoluteXUrl(statusLink),
    text,
    author,
    created_at: createdAt,
    captured_url: window.location.href,
  };
}

function extractAuthor(article, statusLink) {
  const authorHref = statusLink ? statusLink.split("/status/")[0] : "";
  const handle = authorHref.split("/").filter(Boolean).pop();
  if (handle && !["i", "home", "search"].includes(handle)) {
    return handle;
  }
  const userName = article.querySelector('[data-testid="User-Name"]');
  const match = userName?.innerText?.match(/@([A-Za-z0-9_]+)/);
  return match ? match[1] : null;
}

function absoluteXUrl(href) {
  try {
    const url = new URL(href, window.location.origin);
    return `https://x.com${url.pathname}`;
  } catch {
    return href || "";
  }
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
