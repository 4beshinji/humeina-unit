"""API keyを設定ファイルから分離して解決する資格情報ストア。"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class SecretStoreError(RuntimeError):
    """資格情報の読み書きに失敗したことを、秘密値を含めず通知する。"""


def _read_kwallet(reference: Mapping[str, Any]) -> str:
    wallet = str(reference.get("wallet", "kdewallet"))
    folder = str(reference.get("folder", "humeina-unit"))
    entry = str(reference.get("entry", "opencode-go-api-key"))
    try:
        result = subprocess.run(
            ["kwallet-query", "--read-password", entry, "--folder", folder, wallet],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SecretStoreError("KWalletからAPI keyを取得できません") from exc
    secret = result.stdout.rstrip("\r\n")
    if result.returncode != 0 or not secret:
        raise SecretStoreError("KWalletにAPI keyが見つかりません")
    return secret


def write_kwallet_secret(
    secret: str,
    *,
    wallet: str = "kdewallet",
    folder: str = "humeina-unit",
    entry: str = "opencode-go-api-key",
) -> None:
    """秘密値をコマンドラインへ載せず、標準入力からKWalletへ保存する。"""

    if not secret.strip():
        raise SecretStoreError("空のAPI keyは保存できません")
    try:
        result = subprocess.run(
            ["kwallet-query", "--write-password", entry, "--folder", folder, wallet],
            input=secret,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SecretStoreError("KWalletへAPI keyを保存できません") from exc
    if result.returncode != 0:
        raise SecretStoreError("KWalletへAPI keyを保存できません")


def _read_opencode_auth(reference: Mapping[str, Any]) -> str:
    configured = reference.get("path", "~/.local/share/opencode/auth.json")
    path = Path(str(configured)).expanduser()
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise SecretStoreError("OpenCode認証ファイルを読み取れません") from exc
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        raise SecretStoreError("OpenCode認証パスが通常ファイルではありません")
    if file_stat.st_uid != os.getuid() or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise SecretStoreError("OpenCode認証ファイルは所有者専用のmode 0600にしてください")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        credential = data["opencode-go"]
        secret = credential["key"]
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise SecretStoreError("OpenCode Go資格情報を読み取れません") from exc
    if not isinstance(secret, str) or not secret.strip():
        raise SecretStoreError("OpenCode Go API keyが空です")
    return secret


def read_secret(reference: Mapping[str, Any]) -> str | None:
    """明示された安全なsourceから秘密値を取得する。"""

    source = str(reference.get("source", "environment"))
    if source == "environment":
        variable = str(reference.get("variable", "OPENCODE_GO_API_KEY"))
        return os.environ.get(variable) or None
    if source == "kwallet":
        return _read_kwallet(reference)
    if source == "opencode_auth":
        return _read_opencode_auth(reference)
    raise SecretStoreError(f"未対応の資格情報sourceです: {source}")


def hydrate_config_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """空のOpenCode Go keyだけを外部ストアからメモリ上へ注入する。"""

    llm = config.get("llm")
    providers = llm.get("providers") if isinstance(llm, dict) else None
    provider = providers.get("opencode_go") if isinstance(providers, dict) else None
    if not isinstance(provider, dict) or provider.get("api_key"):
        return config
    secrets = config.get("secrets")
    reference = secrets.get("opencode_go") if isinstance(secrets, dict) else None
    if not isinstance(reference, Mapping):
        return config
    secret = read_secret(reference)
    if secret:
        provider["api_key"] = secret
    return config


__all__ = [
    "SecretStoreError",
    "hydrate_config_secrets",
    "read_secret",
    "write_kwallet_secret",
]
