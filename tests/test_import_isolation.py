"""本体の import が周辺機能 package を読み込まないことの検査."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

PERIPHERAL_PACKAGES = ("exvoice", "news", "slack", "studio", "video")
PERIPHERAL_DEPENDENCIES = ("feedparser", "slack_sdk", "PIL")


def _loaded_after_import(module: str) -> dict[str, list[str]]:
    script = f"""
import json, sys
import {module}
loaded = sorted(sys.modules)
print(json.dumps({{
    "packages": sorted({{n.split(".")[1] for n in loaded
        if n.startswith("yomiage.") and n.split(".")[1] in {PERIPHERAL_PACKAGES!r}}}),
    "dependencies": [n for n in {PERIPHERAL_DEPENDENCIES!r} if n in sys.modules],
}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "module", ["yomiage", "yomiage.reader.engine", "yomiage.server", "yomiage.cli"]
)
def test_core_import_does_not_load_peripheral_features(module: str) -> None:
    assert _loaded_after_import(module) == {"packages": [], "dependencies": []}


def test_isolation_probe_detects_loaded_peripheral_package() -> None:
    """検査自体が周辺 package の読み込みを検出できる（失敗系）."""

    assert _loaded_after_import("yomiage.exvoice.manager")["packages"] == ["exvoice"]
