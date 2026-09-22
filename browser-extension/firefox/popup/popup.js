"use strict";

const elements = {
  connection: document.querySelector("#connection"),
  provider: document.querySelector("#provider"),
  message: document.querySelector("#message"),
  progress: document.querySelector("#progress"),
  readingState: document.querySelector("#reading-state"),
  readingCount: document.querySelector("#reading-count"),
  readingProgress: document.querySelector("#reading-progress")
};

let statusTimer = null;
let statusPolling = false;
let activeJobId = null;
const STATUS_POLL_INTERVAL_MS = 3000;

function showMessage(message, error = false) {
  elements.message.textContent = message || "";
  elements.message.classList.toggle("error", error);
}

function setBusy(busy) {
  for (const button of document.querySelectorAll("button:not(#open-options)")) {
    button.disabled = busy;
  }
}

async function saveProvider() {
  const settings = await browser.runtime.sendMessage({ type: "settings:get" });
  await browser.runtime.sendMessage({
    type: "settings:save",
    settings: { ...settings, provider: elements.provider.value }
  });
}

async function start(mode) {
  setBusy(true);
  showMessage("本文を取得しています…");
  try {
    await saveProvider();
    const result = await browser.runtime.sendMessage({ type: "reading:start", mode });
    activeJobId = result.job_id || null;
    showMessage(`${result.character_count ? `${result.character_count}文字を` : ""}読み上げます。`);
    startStatusPolling();
  } catch (error) {
    showMessage(error.message || String(error), true);
  } finally {
    setBusy(false);
  }
}

async function control(action) {
  try {
    await browser.runtime.sendMessage({ type: "reading:control", action });
    showMessage(action === "stop" ? "停止しました。" : action === "pause" ? "一時停止しました。" : "再開しました。");
    await refreshStatus();
  } catch (error) {
    showMessage(error.message || String(error), true);
  }
}

function renderStatus(status) {
  const total = Number(status.total_utterances || status.total_chunks || 0);
  const current = Number(status.utterance || status.chunk || 0);
  const percent = total > 0 ? Math.min(100, ((current + 1) / total) * 100) : 0;
  elements.progress.hidden = !status.running;
  elements.readingState.textContent = status.paused ? "一時停止中" : status.running ? "読み上げ中" : "待機中";
  elements.readingCount.textContent = total > 0 ? `${Math.min(current + 1, total)} / ${total}` : "";
  elements.readingProgress.value = percent;
}

async function refreshStatus() {
  try {
    const status = await browser.runtime.sendMessage({ type: "server:status" });
    renderStatus(status);
    if (activeJobId) {
      const job = await browser.runtime.sendMessage({
        type: "job:status",
        jobId: activeJobId
      });
      if (job.state === "failed") {
        showMessage(job.error?.message || "読み上げ処理に失敗しました。", true);
        const finishedJobId = activeJobId;
        activeJobId = null;
        await browser.runtime.sendMessage({ type: "job:finished", jobId: finishedJobId });
      } else if (["completed", "cancelled", "partial", "complete_with_warnings"].includes(job.state)) {
        const finishedJobId = activeJobId;
        activeJobId = null;
        await browser.runtime.sendMessage({ type: "job:finished", jobId: finishedJobId });
      }
    }
    if (!status.running && !activeJobId) {
      stopStatusPolling();
    }
  } catch (_error) {
    // 接続表示側で扱う。ポーリング中の一時的な失敗ではメッセージを上書きしない。
  }
}

function stopStatusPolling() {
  statusPolling = false;
  if (statusTimer) {
    clearTimeout(statusTimer);
    statusTimer = null;
  }
}

async function pollStatus() {
  statusTimer = null;
  await refreshStatus();
  if (statusPolling) {
    statusTimer = setTimeout(pollStatus, STATUS_POLL_INTERVAL_MS);
  }
}

function startStatusPolling() {
  stopStatusPolling();
  statusPolling = true;
  void pollStatus();
}

async function initialize() {
  const settings = await browser.runtime.sendMessage({ type: "settings:get" });
  elements.provider.value = settings.provider;
  try {
    const capabilities = await browser.runtime.sendMessage({ type: "server:capabilities" });
    elements.connection.textContent = "サーバー接続済み";
    elements.connection.className = "status connected";
    const available = new Set(capabilities.providers || []);
    for (const option of elements.provider.options) {
      if (option.value !== "auto") {
        option.disabled = !available.has(option.value);
      }
    }
    if (elements.provider.selectedOptions[0]?.disabled) {
      elements.provider.value = "auto";
    }
    const stored = await browser.storage.local.get("lastJobId");
    activeJobId = stored.lastJobId || null;
    if (activeJobId) {
      startStatusPolling();
    } else {
      await refreshStatus();
    }
  } catch (error) {
    elements.connection.textContent = "サーバー未接続";
    elements.connection.className = "status failed";
    showMessage(error.message || String(error), true);
  }
}

document.querySelector("#read-smart").addEventListener("click", () => start("smart"));
document.querySelector("#read-page").addEventListener("click", () => start("article"));
document.querySelector("#read-url").addEventListener("click", () => start("url"));
document.querySelector("#pause").addEventListener("click", () => control("pause"));
document.querySelector("#resume").addEventListener("click", () => control("resume"));
document.querySelector("#stop").addEventListener("click", () => control("stop"));
document.querySelector("#open-options").addEventListener("click", () => browser.runtime.openOptionsPage());
window.addEventListener("unload", stopStatusPolling);

initialize();
