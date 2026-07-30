"""Configuration loading — YAML + environment variables.

``load_config`` remains the legacy raw-dict entry point. New code should call
``load_settings`` and pass the resulting :class:`~yomiage.settings.Settings`
object across application boundaries. Keeping both APIs during the migration
allows the existing Provider and batch implementations to continue working.
"""

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml
from loguru import logger

from .resource_loader import packaged_config_path, packaged_config_yaml

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def _resolve_env_vars(value: str, env: Mapping[str, str] | None = None) -> str:
    """Resolve ${VAR:-default} patterns in config values."""

    env = os.environ if env is None else env

    def _replace(match: re.Match) -> str:
        var_expr = match.group(1)
        if ":-" in var_expr:
            var_name, default = var_expr.split(":-", 1)
            return env.get(var_name, default)
        return env.get(var_expr, "")

    return re.sub(r"\$\{([^}]+)}", _replace, value)


def _walk_resolve(obj, env: Mapping[str, str] | None = None):
    """Recursively resolve env vars in config dict."""
    if isinstance(obj, str):
        return _resolve_env_vars(obj, env)
    if isinstance(obj, dict):
        return {k: _walk_resolve(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_resolve(v, env) for v in obj]
    return obj


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge nested YAML mappings while replacing scalar/list values."""

    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as file:
        value = yaml.safe_load(file) or {}
    if not isinstance(value, dict):
        raise ValueError(f"configuration file must contain a mapping: {path}")
    return value


def _load_config_bundle(config_dir: Path) -> dict[str, Any]:
    """Load the supported YAML files from one config directory."""

    config: dict[str, Any] = {}
    for name in ("default", "voices", "scene_params", "math_dict"):
        path = config_dir / f"{name}.yaml"
        data = _load_yaml(path)
        if not data:
            if not path.exists():
                logger.debug(f"Config file not found: {path}")
            continue
        if name == "default":
            config = _deep_merge(config, data)
        elif name == "math_dict":
            config["math_dict"] = data
        else:
            config[name] = data
    # A user config is often a single ``config.yaml``/``settings.yaml`` file
    # inside its directory. Apply it after the named bundle files so the source
    # itself has one deterministic final layer.
    for name in ("config.yaml", "settings.yaml"):
        path = config_dir / name
        if path.is_file():
            config = _deep_merge(config, _load_yaml(path))
    return config


def _load_packaged_config_bundle() -> dict[str, Any]:
    """Load the immutable standard bundle through ``importlib.resources``."""

    config: dict[str, Any] = {}
    for name in ("default.yaml", "voices.yaml", "scene_params.yaml", "math_dict.yaml"):
        try:
            data = packaged_config_yaml(name)
        except FileNotFoundError:
            # A broken wheel should fail the packaging smoke test, but keeping
            # this diagnostic explicit is more useful than a later KeyError.
            logger.debug(f"Packaged config resource not found: {name}")
            continue
        if name == "default.yaml":
            config = _deep_merge(config, data)
        elif name == "math_dict.yaml":
            config["math_dict"] = data
        else:
            config[name.removesuffix(".yaml")] = data
    if not config:
        raise FileNotFoundError("packaged configuration resources are unavailable")
    return config


def _load_config_source(source: Path | None) -> dict[str, Any]:
    """Load either a config directory or a single YAML mapping."""

    if source is None:
        return {}
    source = Path(source).expanduser().resolve()
    if source.is_dir():
        return _load_config_bundle(source)
    if source.suffix.lower() in {".yaml", ".yml"}:
        return _load_yaml(source)
    if source.exists():
        raise ValueError(f"configuration source must be a directory or YAML file: {source}")
    return {}


def load_config(
    config_dir: Path | None = None,
    *,
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load legacy raw configuration with deterministic precedence.

    The return value intentionally remains a ``dict`` for old Provider and
    source implementations. New application code should use ``load_settings``.
    ``config_dir`` is the packaged/default source; ``user_config_dir`` overlays
    it, then environment interpolation/``YOMIAGE_*`` overrides and explicit
    ``overrides`` are applied.
    """

    env_values = dict(os.environ if env is None else env)
    configured_packaged = env_values.get("YOMIAGE_PACKAGED_CONFIG_DIR") or env_values.get(
        "YOMIAGE_CONFIG_DIR"
    )
    if config_dir is not None:
        config_root = Path(config_dir).expanduser().resolve()
        config = _load_config_source(config_root)
    elif configured_packaged:
        config_root = Path(configured_packaged).expanduser().resolve()
        config = _load_config_source(config_root)
    else:
        # Do not resolve the repository's ``config/`` directory here.  It is
        # intentionally not part of the installed wheel; standard resources
        # are loaded via importlib.resources instead.
        config_root = packaged_config_path()
        config = _load_packaged_config_bundle()
    user_root = user_config_dir
    if user_root is None:
        configured = env_values.get("YOMIAGE_USER_CONFIG_DIR")
        user_root = Path(configured).expanduser() if configured else None
    if user_root is not None:
        # Keep packaged profile defaults in the user-editable boundary even
        # when callers pass ``user_config_dir`` directly instead of an env var.
        env_values.setdefault("YOMIAGE_USER_CONFIG_DIR", str(Path(user_root).expanduser()))

    if user_root is not None:
        config = _deep_merge(config, _load_config_source(user_root))
    config = _walk_resolve(config, env_values)
    # Explicit YOMIAGE_* controls are the environment layer, and therefore
    # override values embedded directly in user YAML as well as ${VAR} values.
    environment_layer = _environment_overrides(env_values)
    if environment_layer:
        config = _deep_merge(config, environment_layer)
    if overrides:
        config = _apply_canonical_overrides(config, overrides)
        config = _walk_resolve(config, env_values)
    return config


def _environment_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Map explicit ``YOMIAGE_*`` variables to typed-setting keys.

    YAML interpolation remains supported for provider-specific variables such as
    ``VOISONA_USERNAME``. These names cover runtime paths and controls that are
    otherwise easy to accidentally leave CWD-dependent.
    """

    result: dict[str, Any] = {}

    def first(*names: str) -> str | None:
        for name in names:
            value = env.get(name)
            if value is not None:
                return value
        return None

    def put(section: str, key: str, *names: str, cast: Any = str) -> None:
        value = first(*names)
        if value is not None:
            result.setdefault(section, {})[key] = cast(value)

    put(
        "tts",
        "primary_provider",
        "YOMIAGE_TTS_PRIMARY_PROVIDER",
        "YOMIAGE_TTS__PRIMARY_PROVIDER",
    )
    put(
        "tts",
        "fallback_provider",
        "YOMIAGE_TTS_FALLBACK_PROVIDER",
        "YOMIAGE_TTS__FALLBACK_PROVIDER",
    )
    put("tts", "lookahead_chunks", "YOMIAGE_TTS_LOOKAHEAD_CHUNKS", cast=int)
    put("tts", "max_chunk_chars", "YOMIAGE_TTS_MAX_CHUNK_CHARS", cast=int)
    put("batch", "synth_concurrency", "YOMIAGE_BATCH_SYNTH_CONCURRENCY", cast=int)
    put("server", "host", "YOMIAGE_SERVER_HOST")
    put("server", "port", "YOMIAGE_SERVER_PORT", cast=int)
    put("server", "max_request_chars", "YOMIAGE_SERVER_MAX_REQUEST_CHARS", cast=int)
    put("server", "max_body_bytes", "YOMIAGE_SERVER_MAX_BODY_BYTES", cast=int)
    put("server", "max_text_chars", "YOMIAGE_SERVER_MAX_TEXT_CHARS", cast=int)
    put(
        "server",
        "max_url_response_bytes",
        "YOMIAGE_SERVER_MAX_URL_RESPONSE_BYTES",
        cast=int,
    )
    put("server", "max_concurrent_jobs", "YOMIAGE_SERVER_MAX_CONCURRENT_JOBS", cast=int)
    put("server", "job_queue_size", "YOMIAGE_SERVER_JOB_QUEUE_SIZE", cast=int)
    put("server", "max_active_jobs", "YOMIAGE_SERVER_MAX_ACTIVE_JOBS", cast=int)
    put("server", "client_rate_limit", "YOMIAGE_SERVER_CLIENT_RATE_LIMIT", cast=int)
    put(
        "server",
        "client_rate_window_seconds",
        "YOMIAGE_SERVER_CLIENT_RATE_WINDOW_SECONDS",
        cast=float,
    )
    put("server", "api_token", "YOMIAGE_API_TOKEN")
    put("storage", "packaged_config_dir", "YOMIAGE_PACKAGED_CONFIG_DIR")
    put("storage", "user_config_dir", "YOMIAGE_USER_CONFIG_DIR")
    put("storage", "data_dir", "YOMIAGE_DATA_DIR")
    put("storage", "output_dir", "YOMIAGE_OUTPUT_DIR")
    put("storage", "assets_dir", "YOMIAGE_ASSETS_DIR")
    put("storage", "sqlite_path", "YOMIAGE_SQLITE_PATH")
    put("storage", "ffmpeg_executable", "YOMIAGE_FFMPEG_EXECUTABLE")

    # Provider enablement and secrets are explicit overrides so a user config
    # cannot silently win over an operator-provided environment variable.
    for name in ("voisona", "voicevox", "voicepeak"):
        section_overrides: dict[str, Any] = {}
        enabled = first(f"YOMIAGE_{name.upper()}_ENABLED")
        if enabled is not None:
            section_overrides["enabled"] = enabled.lower() in {"1", "true", "yes", "on"}
        for key in ("url", "username", "password", "api_key"):
            value = first(
                f"YOMIAGE_{name.upper()}_{key.upper()}",
                f"{name.upper()}_{key.upper()}",
            )
            if value is not None:
                section_overrides[key] = value
        path = first(
            f"{name.upper()}_PATH",
            f"YOMIAGE_{name.upper()}_PATH",
            f"YOMIAGE_{name.upper()}_EXECUTABLE",
        )
        if path is not None:
            section_overrides["path"] = path
        if section_overrides:
            result.setdefault("providers", {})[name] = section_overrides
            # Keep the same precedence visible to legacy raw-dict consumers.
            result[name] = dict(section_overrides)

    for key in ("backend", "url", "model", "summary_model", "api_key"):
        names = [f"YOMIAGE_LLM_{key.upper()}"]
        if key == "api_key":
            names.extend(("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"))
        value = first(*names)
        if value is not None:
            result.setdefault("llm", {})[key] = value
    return result


def _apply_canonical_overrides(
    config: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Make the final explicit layer win over canonical env/provider sections."""

    result = _deep_merge(config, overrides)
    providers = result.setdefault("providers", {})
    explicit_providers = overrides.get("providers")
    if isinstance(explicit_providers, dict):
        for name, section in explicit_providers.items():
            if isinstance(section, dict):
                providers[name] = _deep_merge(providers.get(name, {}), section)
            else:
                providers[name] = deepcopy(section)
    for name in ("voisona", "voicevox", "voicepeak"):
        section = overrides.get(name)
        if isinstance(section, dict):
            providers[name] = _deep_merge(providers.get(name, {}), section)
    # Legacy ``ollama`` is accepted as the explicit spelling for the LLM model.
    if "llm" not in overrides and isinstance(overrides.get("ollama"), dict):
        result["llm"] = _deep_merge(result.get("llm", {}), overrides["ollama"])
    return result


def load_settings(
    config_dir: Path | None = None,
    *,
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
    base_dir: Path | None = None,
    validate_active: bool = True,
):
    """Load and validate :class:`~yomiage.settings.Settings`.

    ``validate_active=False`` is useful for tooling that only wants to inspect
    paths or provider choices without contacting services. Production startup
    should keep the strict default so missing secrets and executables fail before
    workers are created.
    """

    from .settings import Settings

    env_values = dict(os.environ if env is None else env)
    configured_packaged = env_values.get("YOMIAGE_PACKAGED_CONFIG_DIR") or env_values.get(
        "YOMIAGE_CONFIG_DIR"
    )
    config_source = config_dir
    if config_source is None and configured_packaged:
        config_source = Path(configured_packaged)
    config_root = (
        Path(config_source).expanduser().resolve()
        if config_source is not None
        else packaged_config_path()
    )
    if user_config_dir is None:
        configured_user = env_values.get("YOMIAGE_USER_CONFIG_DIR")
        user_config_dir = (
            Path(configured_user).expanduser()
            if configured_user
            else (Path.home() / ".config" / "humeina-unit")
        )
    config = load_config(
        config_source,
        user_config_dir=user_config_dir,
        env=env_values,
        overrides=_environment_overrides(env_values),
    )
    storage = config.setdefault("storage", {})
    if isinstance(storage, dict):
        storage.setdefault("packaged_config_dir", config_root)
        storage.setdefault("user_config_dir", Path(user_config_dir).expanduser())
    if overrides:
        config = _apply_canonical_overrides(config, overrides)
    root = base_dir or Path.cwd()
    return Settings.from_dict(config, base_dir=root, validate_active=validate_active)


def get_tts_config(config: dict | Any, provider: str) -> dict[str, Any]:
    """Get provider-specific TTS config from either raw dict or Settings."""
    if hasattr(config, "providers"):
        providers = config.providers
        target = providers.get(provider)
        return target.to_legacy_dict() if target is not None else {}
    return config.get(provider, {})


# Public re-exports keep ``from yomiage.config import Settings`` working while
# the implementation lives in its own dependency-free model module.
from .settings import (  # noqa: E402  (import after legacy helpers)
    ApplicationSettings,
    AppSettings,
    BatchSettings,
    ConfigPaths,
    LLMSettings,
    PathSettings,
    PathsSettings,
    ProviderSettings,
    ProvidersSettings,
    RuntimePaths,
    ServerSettings,
    Settings,
    SettingsPaths,
    StorageSettings,
    TTSSettings,
)

__all__ = [
    "AppSettings",
    "ApplicationSettings",
    "BatchSettings",
    "ConfigPaths",
    "LLMSettings",
    "PathSettings",
    "PathsSettings",
    "ProviderSettings",
    "ProvidersSettings",
    "RuntimePaths",
    "SettingsPaths",
    "ServerSettings",
    "Settings",
    "StorageSettings",
    "TTSSettings",
    "get_tts_config",
    "load_config",
    "load_settings",
]
