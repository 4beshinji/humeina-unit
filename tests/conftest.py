"""単体テストの既定保存先をユーザーの実データから隔離する。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_directories(request, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    if "e2e" in request.node.keywords:
        return
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    for name in (
        "YOMIAGE_USER_CONFIG_DIR", "YOMIAGE_CONFIG_DIR", "YOMIAGE_PACKAGED_CONFIG_DIR",
        "YOMIAGE_DATA_DIR", "YOMIAGE_OUTPUT_DIR", "YOMIAGE_ENV_FILE", "VOISONA_SHARED_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
