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
from .runtime_paths import data_dir
from .runtime_paths import user_config_dir as default_user_config_dir


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


def _load_config_source(
    source: Path | None, env: Mapping[str, str], *, base_dir: Path | None = None
) -> dict[str, Any]:
    """Load either a config directory or a single YAML mapping."""

    if source is None:
        return {}
    source = Path(source).expanduser().resolve()
    if source.is_dir():
        config = _load_config_bundle(source)
        root = source
    elif source.suffix.lower() in {".yaml", ".yml"}:
        config = _load_yaml(source)
        root = source.parent
    elif source.exists():
        raise ValueError(f"configuration source must be a directory or YAML file: {source}")
    else:
        return {}
    return _resolve_paths(_walk_resolve(config, env), base_dir or root)


def load_config(
    config_dir: Path | None = None,
    *,
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Load legacy raw configuration with deterministic precedence.

    The return value intentionally remains a ``dict`` for old Provider and
    source implementations. New application code should use ``load_settings``.
    ``config_dir`` is the packaged/default source; ``user_config_dir`` overlays
    it, then environment interpolation/``YOMIAGE_*`` overrides and explicit
    ``overrides`` are applied.
    """

    env_values = _config_environment(env, user_config_dir)
    user_source = user_config_dir or env_values.get("YOMIAGE_USER_CONFIG_DIR")
    user_root = Path(user_source).expanduser().resolve() if user_source else (
        default_user_config_dir(env_values)
    )
    user_directory = user_root.parent if user_root.suffix in {".yaml", ".yml"} else user_root
    path_base = base_dir or user_directory
    interpolation_env = dict(env_values)
    interpolation_env.setdefault("YOMIAGE_USER_CONFIG_DIR", str(user_directory))
    configured = env_values.get("YOMIAGE_PACKAGED_CONFIG_DIR") or env_values.get(
        "YOMIAGE_CONFIG_DIR"
    )
    config_source = config_dir or (Path(configured) if configured else None)
    config = (
        _load_config_source(config_source, interpolation_env, base_dir=base_dir)
        if config_source is not None else _load_packaged_config_bundle()
    )
    config = _deep_merge(config, _load_config_source(
        user_root, interpolation_env, base_dir=base_dir
    ))
    config = _walk_resolve(config, interpolation_env)
    environment_layer = _resolve_paths(_environment_overrides(env_values), path_base)
    config = _deep_merge(config, environment_layer)
    if overrides:
        config = _apply_canonical_overrides(config, _resolve_paths(
            _walk_resolve(overrides, interpolation_env), path_base
        ))
    storage = _deep_merge(config.pop("paths", {}), config.get("storage", {}))
    config["storage"] = storage
    for alias, canonical in (("data", "data_dir"), ("output", "output_dir"),
                             ("assets", "assets_dir"), ("job_db_path", "sqlite_path"),
                             ("database_path", "sqlite_path")):
        if alias in storage and canonical not in storage:
            storage[canonical] = storage[alias]
    storage.setdefault("user_config_dir", str(user_directory))
    storage.setdefault("data_dir", str(data_dir(env_values)))
    runtime_data = Path(storage["data_dir"])
    storage.setdefault("sqlite_path", str(runtime_data / "jobs.sqlite3"))
    batch = config.setdefault("batch", {})
    batch["output_dir"] = storage.get("output_dir") or batch.get("output_dir") or str(
        runtime_data / "output"
    )
    storage["output_dir"] = batch["output_dir"]
    video = config.setdefault("video", {})
    video["assets_dir"] = storage.get("assets_dir") or video.get("assets_dir") or str(
        runtime_data / "assets"
    )
    storage["assets_dir"] = video["assets_dir"]
    for kind in ("voice", "voicevox", "voicepeak"):
        key = f"{kind}_profile_dir"
        batch[key] = batch.get(key) or str(user_directory / f"{kind}_profiles")
    # 旧 batch 設定は入力時だけ受け付け、通信層は provider 設定を使う。
    for name, section in config.get("providers", {}).items():
        config[name] = _deep_merge(config.get(name, {}), section)
    voisona = config.setdefault("voisona", {})
    voisona["shared_dir"] = voisona.get("shared_dir") or str(runtime_data / "voisona")
    voisona.setdefault("vm_mount", batch.get("voisona_vm_mount", "Z:"))
    return _resolve_paths(config, path_base)


def _config_environment(
    env: Mapping[str, str] | None, user_source: Path | None = None
) -> dict[str, str]:
    """暗黙の親ディレクトリ探索をせず、明示した .env かユーザー設定を読む。"""
    values = dict(os.environ if env is None else env)
    if env is not None:
        return values
    from dotenv import dotenv_values

    root = Path(user_source).expanduser() if user_source else default_user_config_dir(values)
    if root.suffix in {".yaml", ".yml"}:
        root = root.parent
    env_file = Path(values.get("YOMIAGE_ENV_FILE") or root / ".env").expanduser()
    local = {key: value for key, value in dotenv_values(env_file).items() if value is not None}
    return {**local, **values}


def _resolve_paths(config: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    from .settings import _normalize_paths_in_data

    result = _normalize_paths_in_data(config, base_dir=base_dir)
    # 旧 Consumer も JSON/YAML 化可能な文字列を受け取る。
    def strings(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {key: strings(item) for key, item in value.items()}
        return value
    return strings(result)


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
    put("voisona", "shared_dir", "VOISONA_SHARED_DIR")
    put("voisona", "vm_mount", "VOISONA_VM_MOUNT")
    put("ex_voice", "wav_dir", "EX_VOICE_WAV_DIR")
    put("slack", "bot_token", "SLACK_BOT_TOKEN")
    put("slack", "app_token", "SLACK_APP_TOKEN")

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
        if section_overrides or name in result:
            merged = {**result.get(name, {}), **section_overrides}
            result.setdefault("providers", {})[name] = merged
            # Keep the same precedence visible to legacy raw-dict consumers.
            result[name] = dict(merged)

    backend = first("YOMIAGE_LLM_BACKEND")
    local: dict[str, Any] = {}
    if backend is not None:
        local["type"] = "ollama_legacy" if backend == "ollama" else backend
    for target, names in {
        "base_url": ("YOMIAGE_LLM_URL", "LLAMA_CPP_URL"),
        "model": ("YOMIAGE_LLM_MODEL", "LLAMA_CPP_MODEL"),
        "api_key": ("YOMIAGE_LLM_API_KEY", "LLAMA_CPP_API_KEY"),
    }.items():
        value = first(*names)
        if value is not None:
            local[target] = value
    if local:
        result.setdefault("llm", {}).setdefault("providers", {})["local"] = local
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

    env_values = _config_environment(env, user_config_dir)
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
            else default_user_config_dir(env_values)
        )
    config = load_config(
        config_source,
        user_config_dir=user_config_dir,
        env=env_values,
        overrides=overrides,
        base_dir=base_dir,
    )
    storage = config.setdefault("storage", {})
    if isinstance(storage, dict):
        storage.setdefault("packaged_config_dir", config_root)
        storage.setdefault("user_config_dir", Path(user_config_dir).expanduser())
    root = base_dir or default_user_config_dir(env_values)
    return Settings.from_dict(config, base_dir=root, validate_active=validate_active)


def get_tts_config(config: dict | Any, provider: str) -> dict[str, Any]:
    """Get provider-specific TTS config from either raw dict or Settings."""
    if hasattr(config, "providers"):
        providers = config.providers
        target = providers.get(provider)
        return target.to_legacy_dict() if target is not None else {}
    values = dict(config.get(provider, {}))
    if provider == "voisona":
        values.setdefault("vm_mount", config.get("batch", {}).get("voisona_vm_mount", "Z:"))
    return values


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
