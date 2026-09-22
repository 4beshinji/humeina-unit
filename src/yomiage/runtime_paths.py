"""チェックアウト・起動ディレクトリから独立した既定の保存先。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def _xdg_dir(variable: str, fallback: str, env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    home = Path(values.get("HOME") or Path.home()).expanduser()
    value = values.get(variable)
    root = Path(value).expanduser() if value else home / fallback
    if not root.is_absolute():
        raise ValueError(f"{variable} must be an absolute path")
    return root / "humeina-unit"


def user_config_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("YOMIAGE_USER_CONFIG_DIR")
    if configured:
        path = Path(configured).expanduser().resolve()
        return path.parent if path.suffix in {".yaml", ".yml"} else path
    return _xdg_dir("XDG_CONFIG_HOME", ".config", values)


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("YOMIAGE_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else _xdg_dir(
        "XDG_DATA_HOME", ".local/share", values
    )


def output_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("YOMIAGE_OUTPUT_DIR")
    return Path(configured).expanduser().resolve() if configured else data_dir(values) / "output"


def profile_dir(kind: str, config: Mapping | None = None) -> Path:
    """保存・検索で同じプロファイルディレクトリを使う。"""
    key = f"{kind}_profile_dir"
    value = (config or {}).get("batch", {}).get(key)
    return Path(value).expanduser().resolve() if value else user_config_dir() / f"{kind}_profiles"
