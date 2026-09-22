"use strict";

const DEFAULT_SETTINGS = Object.freeze({
  serverUrl: "http://127.0.0.1:8030",
  apiToken: "",
  provider: "auto",
  extractionMode: "smart"
});

const MENU_READ_PAGE = "humeina-read-page";
const MENU_READ_SELECTION = "humeina-read-selection";
const JOB_STATUS_INTERVAL_MS = 6000;
const TERMINAL_JOB_STATES = new Set([
  "completed",
  "cancelled",
  "partial",
  "complete_with_warnings",
  "failed"
]);

let watchedJobId = null;
let jobStatusTimer = null;

async function getSettings() {
  const stored = await browser.storage.local.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

function normalizeServerUrl(value) {
  const parsed = new URL(String(value || "").trim());
  const loopback = parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost";
  if (parsed.protocol !== "http:" || !loopback) {
    throw new Error("サーバーURLは http://127.0.0.1 または http://localhost を指定してください。");
  }
  parsed.pathname = parsed.pathname.replace(/\/+$/, "");
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString().replace(/\/$/, "");
}

async function apiRequest(path, options = {}) {
  const settings = await getSettings();
  const baseUrl = normalizeServerUrl(settings.serverUrl);
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (settings.apiToken) {
    headers.set("Authorization", `Bearer ${settings.apiToken}`);
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...options, headers });
  } catch (_error) {
    throw new Error("読み上げサーバーに接続できません。yomiage serve が起動しているか確認してください。");
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const message = payload?.error?.message || payload?.detail || `HTTP ${response.status}`;
    if (response.status === 409) {
      throw new Error("すでに読み上げ中です。停止してからやり直してください。");
    }
    if (response.status === 413) {
      throw new Error("抽出した本文が長すぎます。選択範囲を読み上げるか、上限設定を変更してください。");
    }
    throw new Error(String(message));
  }
  return payload;
}

function isReadablePageUrl(url) {
  return /^https?:\/\//i.test(url || "");
}

async function extractFromTab(tabId, mode) {
  await browser.scripting.executeScript({
    target: { tabId },
    files: ["content/extractor.js"]
  });
  const result = await browser.tabs.sendMessage(tabId, {
    type: "humeina:extract",
    mode
  });
  if (!result?.text?.trim()) {
    throw new Error("このページから読み上げ可能な本文を抽出できませんでした。");
  }
  return result;
}

async function activeTab() {
  const tabs = await browser.tabs.query({ active: true, currentWindow: true });
  if (!tabs[0]?.id) {
    throw new Error("読み上げるタブが見つかりません。");
  }
  return tabs[0];
}

async function startReading({ tabId, mode, selectionText, title, url } = {}) {
  const settings = await getSettings();
  const tab = tabId ? await browser.tabs.get(tabId) : await activeTab();
  const sourceUrl = url || tab.url || "";
  const sourceTitle = title || tab.title || "";
  const selectedMode = mode || settings.extractionMode;

  let payload;
  if (selectedMode === "url") {
    if (!isReadablePageUrl(sourceUrl)) {
      throw new Error("このタブはURL経由で読み上げできません。");
    }
    payload = await apiRequest("/api/yomiage/read", {
      method: "POST",
      body: JSON.stringify({ url: sourceUrl, provider: settings.provider })
    });
  } else {
    let extracted;
    if (selectionText?.trim()) {
      extracted = {
        text: selectionText.trim(),
        title: sourceTitle,
        url: sourceUrl,
        sourceKind: "selection"
      };
    } else {
      if (!isReadablePageUrl(sourceUrl)) {
        throw new Error("Firefoxの内部ページでは本文を取得できません。");
      }
      extracted = await extractFromTab(tab.id, selectedMode);
    }
    payload = await apiRequest("/api/yomiage/read-text", {
      method: "POST",
      body: JSON.stringify({
        text: extracted.text,
        source_url: extracted.url || sourceUrl,
        title: extracted.title || sourceTitle,
        source_kind: extracted.sourceKind || "article",
        provider: settings.provider
      })
    });
  }

  const jobId = payload.job_id || null;
  if (jobId) {
    await browser.storage.local.set({ lastJobId: jobId });
    await setBadge("▶", "#2d7d67");
    watchReadingJob(jobId);
  } else {
    await browser.storage.local.remove("lastJobId");
    await setBadge("", "#352d63");
  }
  return payload;
}

async function controlReading(action) {
  const allowed = new Set(["pause", "resume", "stop"]);
  if (!allowed.has(action)) {
    throw new Error("不明な再生操作です。");
  }
  const payload = await apiRequest(`/api/yomiage/${action}`, { method: "POST" });
  if (action === "pause") {
    await setBadge("Ⅱ", "#8a651e");
  } else if (action === "resume") {
    await setBadge("▶", "#2d7d67");
  } else {
    await setBadge("", "#352d63");
  }
  return payload;
}

async function setBadge(text, color) {
  await browser.action.setBadgeBackgroundColor({ color });
  await browser.action.setBadgeText({ text });
}

function stopJobWatcher(jobId = null) {
  if (jobId !== null && watchedJobId !== jobId) {
    return;
  }
  watchedJobId = null;
  if (jobStatusTimer) {
    clearTimeout(jobStatusTimer);
    jobStatusTimer = null;
  }
}

async function finishReadingJob(jobId) {
  const wasWatched = watchedJobId === jobId;
  stopJobWatcher(jobId);
  const stored = await browser.storage.local.get("lastJobId");
  const wasStored = stored.lastJobId === jobId;
  if (wasStored) {
    await browser.storage.local.remove("lastJobId");
  }
  if (wasWatched || wasStored) {
    await setBadge("", "#352d63");
  }
}

async function pollReadingJob(jobId) {
  jobStatusTimer = null;
  if (watchedJobId !== jobId) {
    return;
  }
  try {
    const job = await apiRequest(`/api/yomiage/jobs/${encodeURIComponent(jobId)}`);
    if (TERMINAL_JOB_STATES.has(job.state)) {
      await finishReadingJob(jobId);
      return;
    }
  } catch (_error) {
    // 一時的な接続失敗ではactive表示を消さず、次回の確認を待つ。
  }
  if (watchedJobId === jobId) {
    jobStatusTimer = setTimeout(() => pollReadingJob(jobId), JOB_STATUS_INTERVAL_MS);
  }
}

function watchReadingJob(jobId, { immediate = false } = {}) {
  stopJobWatcher();
  if (!jobId) {
    return;
  }
  watchedJobId = jobId;
  if (immediate) {
    void pollReadingJob(jobId);
  } else {
    jobStatusTimer = setTimeout(() => pollReadingJob(jobId), JOB_STATUS_INTERVAL_MS);
  }
}

async function restoreReadingJobWatcher() {
  const stored = await browser.storage.local.get("lastJobId");
  if (stored.lastJobId) {
    watchReadingJob(stored.lastJobId, { immediate: true });
  }
}

async function notifyError(error) {
  await setBadge("!", "#a63636");
  await browser.notifications.create({
    type: "basic",
    iconUrl: browser.runtime.getURL("icons/icon.svg"),
    title: "Humeina Unit Reader",
    message: error instanceof Error ? error.message : String(error)
  });
}

browser.runtime.onInstalled.addListener(async () => {
  await browser.menus.removeAll();
  browser.menus.create({
    id: MENU_READ_PAGE,
    title: "このページを読み上げる",
    contexts: ["page"]
  });
  browser.menus.create({
    id: MENU_READ_SELECTION,
    title: "選択範囲を読み上げる",
    contexts: ["selection"]
  });
});

browser.menus.onClicked.addListener(async (info, tab) => {
  try {
    if (info.menuItemId === MENU_READ_SELECTION) {
      await startReading({
        tabId: tab.id,
        mode: "selection",
        selectionText: info.selectionText,
        title: tab.title,
        url: tab.url
      });
    } else if (info.menuItemId === MENU_READ_PAGE) {
      await startReading({ tabId: tab.id, mode: "article" });
    }
  } catch (error) {
    await notifyError(error);
  }
});

browser.commands.onCommand.addListener(async (command) => {
  try {
    if (command === "read-current-page") {
      await startReading();
    } else if (command === "stop-reading") {
      await controlReading("stop");
    }
  } catch (error) {
    await notifyError(error);
  }
});

browser.runtime.onMessage.addListener((message) => {
  switch (message?.type) {
    case "settings:get":
      return getSettings();
    case "settings:save":
      return (async () => {
        const next = { ...message.settings };
        next.serverUrl = normalizeServerUrl(next.serverUrl);
        if (!new Set(["auto", "voisona", "voicevox"]).has(next.provider)) {
          throw new Error("不明な音声エンジンです。");
        }
        if (!new Set(["smart", "article", "url"]).has(next.extractionMode)) {
          throw new Error("不明な本文取得方法です。");
        }
        await browser.storage.local.set(next);
        return getSettings();
      })();
    case "server:capabilities":
      return apiRequest("/api/yomiage/capabilities");
    case "server:status":
      return apiRequest("/api/yomiage/status");
    case "job:status":
      return apiRequest(`/api/yomiage/jobs/${encodeURIComponent(message.jobId)}`);
    case "job:finished":
      return finishReadingJob(message.jobId);
    case "reading:start":
      return startReading({ mode: message.mode });
    case "reading:control":
      return controlReading(message.action);
    default:
      return undefined;
  }
});

void restoreReadingJobWatcher();
