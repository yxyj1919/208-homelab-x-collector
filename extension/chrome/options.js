const DEFAULT_API_BASE_URL = "http://127.0.0.1:8765";

const input = document.querySelector("#apiBaseUrl");
const message = document.querySelector("#message");
const saveButton = document.querySelector("#save");
const resetButton = document.querySelector("#reset");

document.addEventListener("DOMContentLoaded", async () => {
  const result = await chrome.storage.local.get({ apiBaseUrl: DEFAULT_API_BASE_URL });
  input.value = normalizeBaseUrl(result.apiBaseUrl);
});

saveButton.addEventListener("click", async () => {
  const value = normalizeBaseUrl(input.value);
  if (!isValidLocalUrl(value)) {
    showMessage("Use http://127.0.0.1:<port> or http://localhost:<port>.", true);
    return;
  }

  await chrome.storage.local.set({ apiBaseUrl: value });
  input.value = value;
  showMessage("Saved.");
  closeOptionsPageSoon();
});

resetButton.addEventListener("click", async () => {
  await chrome.storage.local.set({ apiBaseUrl: DEFAULT_API_BASE_URL });
  input.value = DEFAULT_API_BASE_URL;
  showMessage("Reset to default.");
  closeOptionsPageSoon();
});

function isValidLocalUrl(value) {
  try {
    const parsed = new URL(value);
    return (
      parsed.protocol === "http:" &&
      ["127.0.0.1", "localhost"].includes(parsed.hostname) &&
      parsed.port.length > 0
    );
  } catch {
    return false;
  }
}

function normalizeBaseUrl(value) {
  const raw = String(value || DEFAULT_API_BASE_URL).trim();
  return raw.replace(/\/+$/, "") || DEFAULT_API_BASE_URL;
}

function showMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("error", isError);
}

function closeOptionsPageSoon() {
  window.setTimeout(() => window.close(), 500);
}
