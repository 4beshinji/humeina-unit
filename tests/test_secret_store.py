"""API keyをYAMLから分離する資格情報ストアのcontract test。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from yomiage.secret_store import (
    SecretStoreError,
    hydrate_config_secrets,
    read_secret,
    write_kwallet_secret,
)


def test_opencode_auth_requires_owner_only_permissions(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "test-secret"}}),
        encoding="utf-8",
    )
    auth.chmod(0o600)

    assert read_secret({"source": "opencode_auth", "path": str(auth)}) == "test-secret"

    auth.chmod(0o640)
    with pytest.raises(SecretStoreError, match="0600"):
        read_secret({"source": "opencode_auth", "path": str(auth)})

    auth.chmod(0o600)
    link = tmp_path / "auth-link.json"
    link.symlink_to(auth)
    with pytest.raises(SecretStoreError, match="通常ファイル"):
        read_secret({"source": "opencode_auth", "path": str(link)})


def test_hydrate_only_fills_an_empty_key(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "stored-secret"}}),
        encoding="utf-8",
    )
    auth.chmod(0o600)
    config = {
        "llm": {"providers": {"opencode_go": {"api_key": ""}}},
        "secrets": {"opencode_go": {"source": "opencode_auth", "path": str(auth)}},
    }

    hydrate_config_secrets(config)

    assert config["llm"]["providers"]["opencode_go"]["api_key"] == "stored-secret"


def test_kwallet_uses_stdin_and_never_command_line_for_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        input_value = kwargs.get("input")
        calls.append((command, input_value if isinstance(input_value, str) else None))
        if "--read-password" in command:
            return subprocess.CompletedProcess(command, 0, stdout="stored-secret\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    write_kwallet_secret("new-secret")
    assert read_secret({"source": "kwallet"}) == "stored-secret"
    assert calls[0][1] == "new-secret"
    assert all("new-secret" not in argument for argument in calls[0][0])
