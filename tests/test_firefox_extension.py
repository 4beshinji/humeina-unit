"""Firefox WebExtensionの権限境界と配布物を検証する。"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "browser-extension" / "firefox"


def test_manifest_uses_firefox_manifest_v3_event_page() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"] == {"scripts": ["background.js"]}
    assert manifest["browser_specific_settings"]["gecko"]["id"]
    assert "activeTab" in manifest["permissions"]
    assert "scripting" in manifest["permissions"]


def test_manifest_does_not_request_continuous_access_to_browsing_data() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    hosts = set(manifest["host_permissions"])

    assert "<all_urls>" not in hosts
    assert hosts == {"http://127.0.0.1/*", "http://localhost/*"}
    assert "tabs" not in manifest["permissions"]


def test_manifest_referenced_files_are_present() -> None:
    required = (
        "background.js",
        "content/extractor.js",
        "icons/icon.svg",
        "popup/popup.html",
        "popup/popup.css",
        "popup/popup.js",
        "options/options.html",
        "options/options.css",
        "options/options.js",
    )

    assert all((ROOT / relative).is_file() for relative in required)


def test_background_uses_direct_text_reader_and_local_storage() -> None:
    background = (ROOT / "background.js").read_text(encoding="utf-8")

    assert "/api/yomiage/read-text" in background
    assert "browser.storage.local" in background
    assert "browser.storage.sync" not in background


def test_popup_polling_stays_below_default_server_rate_limit() -> None:
    popup = (ROOT / "popup/popup.js").read_text(encoding="utf-8")
    match = re.search(r"const STATUS_POLL_INTERVAL_MS = (\d+);", popup)

    assert match is not None
    interval_ms = int(match.group(1))
    requests_per_minute = 2 * 60_000 / interval_ms
    assert requests_per_minute < 60
    assert "setTimeout(pollStatus, STATUS_POLL_INTERVAL_MS)" in popup


def test_background_clears_badge_for_terminal_read_jobs() -> None:
    background = (ROOT / "background.js").read_text(encoding="utf-8")
    popup = (ROOT / "popup/popup.js").read_text(encoding="utf-8")

    assert "watchReadingJob(jobId)" in background
    assert "TERMINAL_JOB_STATES.has(job.state)" in background
    assert 'await setBadge("", "#352d63")' in background
    assert 'type: "job:finished"' in popup
