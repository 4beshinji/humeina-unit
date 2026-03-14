"""Configuration loading — YAML + environment variables."""

import os
import re
from pathlib import Path

import yaml
from loguru import logger

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def _resolve_env_vars(value: str) -> str:
    """Resolve ${VAR:-default} patterns in config values."""

    def _replace(match: re.Match) -> str:
        var_expr = match.group(1)
        if ":-" in var_expr:
            var_name, default = var_expr.split(":-", 1)
            return os.getenv(var_name, default)
        return os.getenv(var_expr, "")

    return re.sub(r"\$\{([^}]+)}", _replace, value)


def _walk_resolve(obj):
    """Recursively resolve env vars in config dict."""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _walk_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_resolve(v) for v in obj]
    return obj


def load_config(config_dir: Path | None = None) -> dict:
    """Load and merge all configuration files."""
    config_dir = config_dir or _CONFIG_DIR

    config = {}
    for name in ("default", "voices", "scene_params"):
        path = config_dir / f"{name}.yaml"
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            if name == "default":
                config.update(data)
            else:
                config[name] = data
        else:
            logger.debug(f"Config file not found: {path}")

    config = _walk_resolve(config)
    return config


def get_tts_config(config: dict, provider: str) -> dict:
    """Get provider-specific TTS config."""
    return config.get(provider, {})
