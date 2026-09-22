"use strict";

const form = document.querySelector("#settings");
const message = document.querySelector("#message");

function showMessage(value, error = false) {
  message.textContent = value;
  message.classList.toggle("error", error);
}

function formSettings() {
  return {
    serverUrl: form.elements.serverUrl.value,
    apiToken: form.elements.apiToken.value,
    provider: form.elements.provider.value,
    extractionMode: form.elements.extractionMode.value
  };
}

async function save() {
  return browser.runtime.sendMessage({
    type: "settings:save",
    settings: formSettings()
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await save();
    showMessage("設定を保存しました。");
  } catch (error) {
    showMessage(error.message || String(error), true);
  }
});

document.querySelector("#test").addEventListener("click", async () => {
  try {
    await save();
    const capabilities = await browser.runtime.sendMessage({ type: "server:capabilities" });
    const providers = capabilities.providers?.join(" / ") || "なし";
    showMessage(`接続できました。利用可能な音声エンジン: ${providers}`);
  } catch (error) {
    showMessage(error.message || String(error), true);
  }
});

(async () => {
  const settings = await browser.runtime.sendMessage({ type: "settings:get" });
  form.elements.serverUrl.value = settings.serverUrl;
  form.elements.apiToken.value = settings.apiToken;
  form.elements.provider.value = settings.provider;
  form.elements.extractionMode.value = settings.extractionMode;
})();
