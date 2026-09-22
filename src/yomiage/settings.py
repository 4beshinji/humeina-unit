"""型付きアプリケーション設定と実行時パスの解決。

``config.py`` は長い間、YAMLをそのまま ``dict`` として返していた。この
モジュールは、新しいコードが利用する設定の境界を定義する。旧来の入口を
一度に移行することはCFG-001の対象外なので、:meth:`Settings.to_legacy_dict`
を互換アダプターとして提供する。

設定の優先順位は常に次の順序である。

1. packaged defaults (``config_dir``)
2. user config (``user_config_dir``)
3. environment variables (YAML内の ``${VAR}`` を含む)
4. 明示的な ``overrides``

この順序を ``load_settings`` に集約することで、CLI、API、SDKが同じ解決
結果を得られるようにする。相対パスはロード時のベースディレクトリに対して
絶対化し、プロセスのカレントディレクトリへ後から依存しない。
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .resource_loader import packaged_config_path
from .runtime_paths import data_dir, output_dir, user_config_dir

ProviderName = Literal["voisona", "voicevox", "voicepeak"]
LLMBackendName = Literal["llama_cpp", "opencode_go", "ollama", "openai", "anthropic", "gemini"]

_PROVIDER_NAMES = ("voisona", "voicevox", "voicepeak")
_PATH_FIELDS = {
    "packaged_config_dir",
    "user_config_dir",
    "data_dir",
    "data",
    "output_dir",
    "output",
    "assets_dir",
    "assets",
    "voice_profile_dir",
    "voicevox_profile_dir",
    "voicepeak_profile_dir",
    "exvoice_wav_dir",
    "sqlite_path",
    "database_path",
    "job_db_path",
    "ffmpeg_executable",
    "ffmpeg",
    "ffmpeg_path",
    "external_binary_dir",
    "external_binary",
    "binary_dir",
    "executable_dir",
    "executable",
    "path",
    "wav_dir",
    "shared_dir",
}


def _as_secret(value: Any) -> SecretStr | None:
    """Normalize optional secret values, treating empty strings as unset."""

    if value is None:
        return None
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if not isinstance(value, str):
        raise ValueError("secret must be a string")
    value = value.strip()
    return SecretStr(value) if value else None


def _absolute_path(value: Any, *, base_dir: Path | None = None) -> Path | None:
    """Convert a path-like value to an absolute path without requiring it to exist."""

    if value is None or value == "":
        return None
    if isinstance(value, Path):
        path = value.expanduser()
    elif isinstance(value, str):
        path = Path(value).expanduser()
    else:
        raise ValueError("path must be a string or pathlib.Path")
    if not path.is_absolute():
        path = (base_dir or Path.cwd()) / path
    return path.resolve(strict=False)


def _http_url(value: Any) -> str | None:
    """Validate provider/LLM URLs while keeping private LAN URLs supported."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("url must be a string")
    value = value.strip()
    if not value:
        return None
    if not re.match(r"^https?://[^\s]+$", value, flags=re.IGNORECASE):
        raise ValueError("url must use http:// or https://")
    return value


class TTSSettings(BaseModel):
    """TTSの選択と流量制御設定。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    primary_provider: ProviderName = "voicevox"
    fallback_provider: ProviderName | None = None
    lookahead_chunks: StrictInt = Field(default=3, ge=1, le=100)
    max_chunk_chars: StrictInt = Field(default=200, ge=1, le=100_000)

    @model_validator(mode="after")
    def _validate_fallback(self) -> TTSSettings:
        if self.fallback_provider == self.primary_provider:
            raise ValueError("fallback_provider must differ from primary_provider")
        return self


class ProviderSettings(BaseModel):
    """Provider共通設定。

    ``path`` と ``executable`` はVOICEPEAK等の外部実行ファイル向けに両方
    受け付ける。旧YAMLの ``path`` は ``executable`` と同じ意味で扱う。
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True, populate_by_name=True)

    enabled: StrictBool = True
    url: StrictStr | None = None
    username: StrictStr | None = None
    password: SecretStr | None = None
    api_key: SecretStr | None = None
    default_voice: StrictStr | StrictInt | None = None
    default_speaker: StrictStr | StrictInt | None = None
    default_narrator: StrictStr | None = None
    path: Path | None = None
    executable: Path | None = None

    _validate_url = field_validator("url", mode="before")(_http_url)
    _validate_password = field_validator("password", mode="before")(_as_secret)
    _validate_api_key = field_validator("api_key", mode="before")(_as_secret)

    _resolve_path = field_validator("path", "executable", mode="before")(_absolute_path)

    @model_validator(mode="after")
    def _normalize_executable(self) -> ProviderSettings:
        if self.executable is None and self.path is not None:
            self.executable = self.path
        elif self.path is None and self.executable is not None:
            self.path = self.executable
        return self

    def secret_value(self, field: Literal["password", "api_key"]) -> str | None:
        secret = getattr(self, field)
        return secret.get_secret_value() if secret is not None else None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the provider section expected by legacy provider classes."""

        result: dict[str, Any] = {}
        if self.url is not None:
            result["url"] = self.url
        if self.username is not None:
            result["username"] = self.username
        if self.password is not None:
            result["password"] = self.password.get_secret_value()
        if self.api_key is not None:
            result["api_key"] = self.api_key.get_secret_value()
        if self.default_voice is not None:
            result["default_voice"] = self.default_voice
        if self.default_speaker is not None:
            result["default_speaker"] = self.default_speaker
        if self.default_narrator is not None:
            result["default_narrator"] = self.default_narrator
        if self.executable is not None:
            result["path"] = str(self.executable)
            result["executable"] = str(self.executable)
        if not self.enabled:
            result["enabled"] = False

        # Preserve provider-specific values (language, pitch_scale, etc.) while
        # ensuring secrets and model internals never leak into the dict.
        for key, value in self.model_extra.items():
            if key not in result and key not in {"password", "api_key"}:
                if isinstance(value, SecretStr):
                    value = value.get_secret_value()
                result[key] = value
        return result


class ProvidersSettings(BaseModel):
    """使用可能なTTS Providerの設定。未知名は入力時に拒否する。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    voisona: ProviderSettings = Field(
        default_factory=lambda: ProviderSettings(url="http://localhost:32766")
    )
    voicevox: ProviderSettings = Field(
        default_factory=lambda: ProviderSettings(url="http://localhost:50021")
    )
    voicepeak: ProviderSettings = Field(default_factory=ProviderSettings)

    def __getitem__(self, name: str) -> ProviderSettings:
        if name not in _PROVIDER_NAMES:
            raise KeyError(name)
        return getattr(self, name)

    def get(self, name: str, default: ProviderSettings | None = None) -> ProviderSettings | None:
        return getattr(self, name, default)

    def to_legacy_dict(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name in _PROVIDER_NAMES:
            section = getattr(self, name).to_legacy_dict()
            if name == "voicevox" and "default_voice" in section:
                section.setdefault("default_speaker", section["default_voice"])
            elif name == "voicepeak" and "default_voice" in section:
                section.setdefault("default_narrator", section["default_voice"])
            result[name] = section
        return result


class LLMProviderSettings(BaseModel):
    """一つのLLM接続先。秘密値はrepr/model_dumpでmaskする。"""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    type: StrictStr = "llama_cpp"
    base_url: StrictStr = "http://127.0.0.1:8080/v1"
    api_key: SecretStr | None = None
    model: StrictStr = "Qwen3.8-27B"
    protocol: StrictStr = "chat_completions"
    context_tokens: StrictInt = Field(default=32768, ge=1024)
    enabled: StrictBool = True

    _validate_base_url = field_validator("base_url", mode="before")(_http_url)
    _validate_api_key = field_validator("api_key", mode="before")(_as_secret)

    def to_config_dict(self) -> dict[str, Any]:
        result = self.model_dump(mode="python")
        if self.api_key is not None:
            result["api_key"] = self.api_key.get_secret_value()
        result.update(self.model_extra)
        return result


def _default_llm_providers() -> dict[str, LLMProviderSettings]:
    return {"local": LLMProviderSettings()}


class LLMSettings(BaseModel):
    """Provider、API形式、task routeを分離したLLM設定。"""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    enabled: StrictBool = True
    backend: LLMBackendName = "llama_cpp"
    url: StrictStr | None = "http://127.0.0.1:8080/v1"
    api_key: SecretStr | None = None
    model: StrictStr | None = "Qwen3.8-27B"
    summary_model: StrictStr | None = None
    providers: dict[str, LLMProviderSettings] = Field(default_factory=_default_llm_providers)
    routes: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {"default": {"provider": "local"}}
    )
    fallback: dict[str, Any] = Field(default_factory=lambda: {"order": ["local"]})
    limits: dict[str, Any] = Field(default_factory=dict)

    _validate_url = field_validator("url", mode="before")(_http_url)
    _validate_api_key = field_validator("api_key", mode="before")(_as_secret)

    @field_validator("fallback", mode="before")
    @classmethod
    def _normalize_yaml_boolean_key(cls, value: Any) -> Any:
        if isinstance(value, dict) and True in value and "on" not in value:
            value = dict(value)
            value["on"] = value.pop(True)
        return value

    @model_validator(mode="before")
    @classmethod
    def _convert_legacy_backend(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "providers" in data:
            return data
        if any(key in data for key in ("backend", "url", "model", "summary_model")):
            result = deepcopy(data)
            backend = str(result.get("backend", "ollama"))
            provider_type = "ollama_legacy" if backend == "ollama" else backend
            protocol = {
                "ollama": "ollama_chat",
                "anthropic": "messages",
                "openai": "chat_completions",
                "llama_cpp": "chat_completions",
            }.get(backend, "auto")
            result["providers"] = {
                "local": {
                    "type": provider_type,
                    "base_url": result.get("url") or "http://localhost:11434",
                    "api_key": result.get("api_key") or "",
                    "model": result.get("model") or "qwen3.5:9b",
                    "protocol": protocol,
                }
            }
            result.setdefault("routes", {"default": {"provider": "local"}})
            result.setdefault("fallback", {"order": ["local"]})
            return result
        return data

    @model_validator(mode="after")
    def _validate_backend_requirements(self, info: ValidationInfo) -> LLMSettings:
        if not (info.context or {}).get("validate_active", True):
            return self
        if self.enabled and self.backend in {"openai", "anthropic"} and self.api_key is None:
            raise ValueError(f"api_key is required when LLM backend '{self.backend}' is enabled")
        return self

    def to_config_dict(self) -> dict[str, Any]:
        return {
            "providers": {
                name: provider.to_config_dict() for name, provider in self.providers.items()
            },
            "routes": deepcopy(self.routes),
            "fallback": deepcopy(self.fallback),
            "limits": deepcopy(self.limits),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """旧integrator向け。新規compositionはto_config_dictを使う。"""

        return {
            "url": self.url,
            "model": self.model,
            "summary_model": self.summary_model,
            "api_key": self.api_key.get_secret_value() if self.api_key else None,
            "enabled": self.enabled,
        }


class BatchSettings(BaseModel):
    """バッチ実行設定。特に並列数はstrictに検証する。"""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    output_dir: Path = Field(default_factory=output_dir)
    default_mode: ProviderName = "voisona"
    analysis_window_chars: StrictInt = Field(default=3000, ge=1, le=1_000_000)
    analysis_window_sentences: StrictInt = Field(default=25, ge=1, le=100_000)
    manifest_save_interval: StrictInt = Field(default=10, ge=1, le=100_000)
    silence_duration: StrictFloat = Field(default=1.5, ge=0.0, le=3600.0)
    concat_format: StrictStr = "wav"
    cleanup_after_concat: StrictBool = False
    voisona_vm_mount: StrictStr = "Z:"
    voice_profile_dir: Path | None = None
    voicevox_profile_dir: Path | None = None
    voicepeak_profile_dir: Path | None = None
    synth_concurrency: StrictInt = Field(default=1, ge=1, le=256)

    _resolve_paths = field_validator(
        "output_dir",
        "voice_profile_dir",
        "voicevox_profile_dir",
        "voicepeak_profile_dir",
        mode="before",
    )(_absolute_path)


class ServerSettings(BaseModel):
    """HTTPサーバーの起動設定。"""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    host: StrictStr = "127.0.0.1"
    port: StrictInt = Field(default=8000, ge=1, le=65535)
    api_token: SecretStr | None = None
    max_request_chars: StrictInt = Field(default=100_000, ge=1, le=10_000_000)
    max_concurrent_jobs: StrictInt = Field(default=1, ge=1, le=256)
    # API-002 admission-control settings.  The aliases retain the names used by
    # older raw YAML while exposing explicit limits to typed callers.
    max_body_bytes: StrictInt = Field(default=1_048_576, ge=1, le=100_000_000)
    max_text_chars: StrictInt = Field(
        default=100_000,
        ge=1,
        le=10_000_000,
        validation_alias=AliasChoices("max_text_chars", "max_request_chars"),
    )
    max_url_response_bytes: StrictInt = Field(default=10 * 1024 * 1024, ge=1, le=500_000_000)
    job_queue_size: StrictInt = Field(default=64, ge=1, le=100_000)
    max_active_jobs: StrictInt | None = Field(default=None, ge=1, le=100_000)
    client_rate_limit: StrictInt = Field(default=60, ge=1, le=1_000_000)
    client_rate_window_seconds: StrictFloat = Field(default=60.0, gt=0.0, le=86_400.0)

    _validate_token = field_validator("api_token", mode="before")(_as_secret)


class StorageSettings(BaseModel):
    """設定・ランタイムデータ・成果物・アセットを分離したパス。"""

    model_config = ConfigDict(extra="allow", validate_assignment=True, populate_by_name=True)

    packaged_config_dir: Path = Field(
        default_factory=packaged_config_path,
        validation_alias=AliasChoices("packaged_config_dir", "packaged_config", "config_dir"),
    )
    user_config_dir: Path = Field(
        default_factory=user_config_dir,
        validation_alias=AliasChoices("user_config_dir", "user_config"),
    )
    data_dir: Path = Field(
        default_factory=data_dir,
        validation_alias=AliasChoices("data_dir", "data"),
    )
    output_dir: Path = Field(
        default_factory=output_dir,
        validation_alias=AliasChoices("output_dir", "output"),
    )
    assets_dir: Path = Field(
        default_factory=lambda: data_dir() / "assets",
        validation_alias=AliasChoices("assets_dir", "assets"),
    )
    sqlite_path: Path = Field(
        default_factory=lambda: data_dir() / "jobs.sqlite3",
        validation_alias=AliasChoices("sqlite_path", "database_path", "job_db_path"),
    )
    ffmpeg_executable: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("ffmpeg_executable", "ffmpeg", "ffmpeg_path"),
    )
    external_binary_dir: Path = Field(
        default_factory=lambda: data_dir() / "bin",
        validation_alias=AliasChoices(
            "external_binary_dir", "external_binary", "binary_dir", "executable_dir"
        ),
    )
    exvoice_wav_dir: Path | None = None

    _resolve_paths = field_validator(
        "packaged_config_dir",
        "user_config_dir",
        "data_dir",
        "output_dir",
        "assets_dir",
        "sqlite_path",
        "ffmpeg_executable",
        "external_binary_dir",
        "exvoice_wav_dir",
        mode="before",
    )(_absolute_path)

    # Convenient aliases for callers that use the terminology from the roadmap.
    @property
    def packaged_config(self) -> Path:
        return self.packaged_config_dir

    @property
    def packaged(self) -> Path:
        return self.packaged_config_dir

    @property
    def user_config(self) -> Path:
        return self.user_config_dir

    @property
    def user(self) -> Path:
        return self.user_config_dir

    @property
    def data(self) -> Path:
        return self.data_dir

    @property
    def database_path(self) -> Path:
        return self.sqlite_path

    @property
    def output(self) -> Path:
        return self.output_dir

    @property
    def assets(self) -> Path:
        return self.assets_dir

    @property
    def executable(self) -> Path:
        """外部実行ファイルを格納するディレクトリの互換alias."""

        return self.external_binary_dir

    @property
    def external_binary(self) -> Path:
        return self.external_binary_dir

    @property
    def binaries(self) -> Path:
        return self.external_binary_dir


# Both spellings occur in design discussions and external integrations. Keep a
# descriptive alias without duplicating the model or validation rules.
PathSettings = StorageSettings
PathsSettings = StorageSettings
ConfigPaths = StorageSettings
RuntimePaths = StorageSettings
SettingsPaths = StorageSettings


class AnalysisSettings(BaseModel):
    """発話分析の設定。未指定の旧設定はnovel/fullとして読み取る。"""

    model_config = ConfigDict(extra="forbid")
    content_kind: Literal["plain", "novel", "script"] = "novel"
    llm_policy: Literal["off", "selective", "full"] = "full"
    common_dictionary: str | None = None
    work_dictionary: str | None = None
    suggest_readings: bool = False
    reading_proposals: str | None = None
    context_tokens: int = Field(default=32768, ge=1024)
    context_safety_ratio: float = Field(default=0.8, gt=0, le=1)
    max_output_tokens: int = Field(default=4096, ge=128)
    max_items: int = Field(default=25, ge=1)
    max_requests: int = Field(default=100, ge=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_off(cls, value):
        if isinstance(value, dict) and value.get("llm_policy") is False:
            return {**value, "llm_policy": "off"}
        return value

    @classmethod
    def from_config(cls, config) -> AnalysisSettings:
        raw = config.get("analysis", {})
        if isinstance(raw, cls):
            return raw
        # YAML 1.1ではoffがFalseに解釈されるため明示的に正規化する。
        raw = dict(raw)
        if raw.get("llm_policy") is False:
            raw["llm_policy"] = "off"
        return cls.model_validate(raw)


class Settings(BaseModel):
    """アプリケーション全体の型付き設定。

    ``Settings.model_validate(raw)`` は正規化済みraw YAMLにもそのまま使える。
    YAML形式のトップレベル ``voisona`` / ``voicevox`` / ``voicepeak`` / ``ollama``
    は ``from_dict`` で ``providers`` / ``llm`` へ変換する。
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True, populate_by_name=True)

    tts: TTSSettings = Field(default_factory=TTSSettings)
    providers: ProvidersSettings = Field(
        default_factory=ProvidersSettings,
        validation_alias=AliasChoices("providers", "provider"),
    )
    llm: LLMSettings = Field(default_factory=LLMSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    batch: BatchSettings = Field(default_factory=BatchSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    storage: StorageSettings = Field(
        default_factory=StorageSettings,
        validation_alias=AliasChoices("storage", "paths"),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_shape(cls, data: Any) -> Any:
        """Allow direct ``Settings.model_validate(legacy_yaml)`` calls."""

        if isinstance(data, dict):
            typed = any(
                isinstance(data.get(name), BaseModel)
                for name in (
                    "tts",
                    "providers",
                    "provider",
                    "llm",
                    "batch",
                    "server",
                    "storage",
                    "paths",
                )
            )
            legacy_sections = set(data).intersection((*_PROVIDER_NAMES, "ollama", "paths"))
            if typed and not legacy_sections:
                return data
            if typed:
                data = {
                    key: value.model_dump(mode="python") if isinstance(value, BaseModel) else value
                    for key, value in data.items()
                }
            return _normalize_raw(data, base_dir=Path.cwd().resolve())
        return data

    @model_validator(mode="after")
    def _validate_active_on_model_build(self, info: ValidationInfo) -> Settings:
        # ``from_dict(validate_active=False)`` is intentionally available for
        # inspection tooling. Direct model construction remains strict, which
        # makes a missed startup validation visible to callers.
        context = info.context or {}
        if context.get("validate_active", True):
            self.validate_active()
        return self

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        base_dir: str | Path | None = None,
        validate_active: bool = True,
    ) -> Settings:
        """旧YAML形状を正規化して型付き設定へ変換する。"""

        if not isinstance(data, dict):
            raise TypeError("configuration must be a mapping")
        base = Path(base_dir).expanduser().resolve() if base_dir else Path.cwd().resolve()
        normalized = _normalize_raw(data, base_dir=base)
        return cls.model_validate(normalized, context={"validate_active": validate_active})

    @classmethod
    def from_legacy_dict(
        cls,
        data: dict[str, Any],
        *,
        base_dir: str | Path | None = None,
        validate_active: bool = True,
    ) -> Settings:
        return cls.from_dict(data, base_dir=base_dir, validate_active=validate_active)

    @classmethod
    def from_yaml(
        cls,
        config_dir: str | Path | None = None,
        *,
        user_config_dir: str | Path | None = None,
        env: dict[str, str] | None = None,
        overrides: dict[str, Any] | None = None,
        base_dir: str | Path | None = None,
        validate_active: bool = True,
    ) -> Settings:
        """YAML bundleからSettingsを構築する便利な入口."""

        from .config import load_settings

        return load_settings(
            Path(config_dir) if config_dir is not None else None,
            user_config_dir=(Path(user_config_dir) if user_config_dir is not None else None),
            env=env,
            overrides=overrides,
            base_dir=Path(base_dir) if base_dir is not None else None,
            validate_active=validate_active,
        )

    @property
    def provider(self) -> ProvidersSettings:
        """単数名を使う既存呼び出し向けの読み取り専用alias."""

        return self.providers

    @property
    def paths(self) -> StorageSettings:
        """``settings.paths`` と ``settings.storage`` を同じ契約にする。"""

        return self.storage

    @property
    def active_provider_names(self) -> tuple[str, ...]:
        names = [self.tts.primary_provider]
        if self.tts.fallback_provider and self.tts.fallback_provider not in names:
            names.append(self.tts.fallback_provider)
        return tuple(names)

    def validate_active(self) -> Settings:
        """有効なProvider/LLMだけsecretと外部実行ファイルを検証する。"""

        for index, name in enumerate(self.active_provider_names):
            provider = self.providers[name]
            if not provider.enabled:
                if index == 0:
                    raise ValueError(f"primary TTS provider '{name}' is disabled")
                # A disabled fallback is an explicit opt-out; it must not force
                # credentials or an executable that will never be used.
                continue
            if name == "voisona":
                missing = [
                    field
                    for field, value in (
                        ("username", provider.username),
                        ("password", provider.password),
                    )
                    if value is None or (isinstance(value, str) and not value.strip())
                ]
                if missing:
                    raise ValueError(
                        "voisona requires " + ", ".join(missing) + " when the provider is enabled"
                    )
            elif name == "voicepeak":
                executable = provider.executable or provider.path
                if executable is None:
                    raise ValueError(
                        "voicepeak executable path is required when the provider is enabled"
                    )
                if not executable.is_file():
                    raise ValueError(f"voicepeak executable does not exist: {executable}")
        return self

    def to_legacy_dict(self) -> dict[str, Any]:
        """Provider実装・既存CLIが受け取るraw dictへ変換するadapter。"""

        result: dict[str, Any] = {}
        # model_extra contains sections such as video/news/scene_params. Copy them
        # first so core sections below always win deterministically.
        for key, value in self.model_extra.items():
            result[key] = deepcopy(value)

        result["tts"] = self.tts.model_dump(mode="python")
        result.update(self.providers.to_legacy_dict())
        result["llm"] = self.llm.to_config_dict()
        result["analysis"] = self.analysis.model_dump(mode="python")
        result["batch"] = self.batch.model_dump(mode="python")
        result["server"] = self.server.model_dump(mode="python")
        result["storage"] = self.storage.model_dump(mode="python")
        token = result["server"].get("api_token")
        if isinstance(token, SecretStr):
            result["server"]["api_token"] = token.get_secret_value()
        # Paths are always serialized as strings for old dict consumers.
        for section in ("batch", "storage"):
            for key, value in list(result[section].items()):
                if isinstance(value, Path):
                    result[section][key] = str(value)
        for name in _PROVIDER_NAMES:
            result[name] = {
                key: str(value) if isinstance(value, Path) else value
                for key, value in result[name].items()
            }
        return result

    as_legacy_dict = to_legacy_dict
    to_dict = to_legacy_dict


def _merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep merge mappings while replacing scalar/list values."""

    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _normalize_paths_in_data(data: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    """Absolute化をモデルに渡す前に行う（config fileの相対パスを固定）。"""

    result = deepcopy(data)
    sections = ["storage", "paths", "batch", "ex_voice", "video", "analysis"]
    for section_name in sections:
        section = result.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in list(section):
            if key in (
                _PATH_FIELDS | {"common_dictionary", "work_dictionary", "reading_proposals"}
            ) and isinstance(section[key], (str, Path)):
                section[key] = _absolute_path(section[key], base_dir=base_dir)
                if section_name == "analysis":
                    section[key] = str(section[key])

    for provider_name in _PROVIDER_NAMES:
        section = result.get(provider_name)
        if isinstance(section, dict):
            for key in ("path", "executable", "shared_dir"):
                if isinstance(section.get(key), (str, Path)):
                    section[key] = _absolute_path(section[key], base_dir=base_dir)
    nested_providers = result.get("providers")
    if isinstance(nested_providers, dict):
        for provider_name, section in nested_providers.items():
            if isinstance(section, dict):
                for key in ("path", "executable", "shared_dir"):
                    if isinstance(section.get(key), (str, Path)):
                        section[key] = _absolute_path(section[key], base_dir=base_dir)
    return result


def _normalize_raw(data: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    """Convert legacy top-level provider/ollama sections to canonical fields."""

    source = _normalize_paths_in_data(data, base_dir=base_dir)
    result = deepcopy(source)

    # Reject unknown names in an explicit providers mapping. The TTS literal then
    # rejects unknown primary/fallback names even when no provider map is present.
    nested = source.get("providers")
    if nested is None:
        nested = source.get("provider")
    if nested is not None and not isinstance(nested, dict):
        raise ValueError("providers must be a mapping")
    if isinstance(nested, dict):
        unknown = set(nested) - set(_PROVIDER_NAMES)
        if unknown:
            raise ValueError(f"unknown TTS provider(s): {', '.join(sorted(unknown))}")

    provider_map: dict[str, Any] = deepcopy(nested) if isinstance(nested, dict) else {}
    for name in _PROVIDER_NAMES:
        section = source.get(name)
        if isinstance(section, dict):
            # Canonical ``providers`` (used by environment/explicit overrides)
            # has higher precedence than the legacy top-level section.
            provider_map[name] = _merge_dict(section, provider_map.get(name, {}))
    result["providers"] = provider_map
    # Do not retain legacy provider sections as ``model_extra``: that would
    # duplicate passwords in repr/model_dump and undermine SecretStr masking.
    for name in _PROVIDER_NAMES:
        result.pop(name, None)
    result.pop("provider", None)

    # Canonical ``llm`` wins over the legacy ``ollama`` key if both are present.
    if (
        "ollama" in source
        and source["ollama"] is not None
        and not isinstance(source["ollama"], dict)
    ):
        raise ValueError("ollama must be a mapping")
    if "llm" not in result and isinstance(source.get("ollama"), dict):
        result["llm"] = deepcopy(source["ollama"])
    result.pop("ollama", None)

    # ``batch.output_dir`` historically owned the output path. Storage wins only
    # when explicitly set; otherwise mirror the batch value.
    paths_value = result.get("paths")
    if paths_value is not None and not isinstance(paths_value, dict):
        raise ValueError("paths must be a mapping")
    paths = paths_value if isinstance(paths_value, dict) else {}
    storage_value = result.get("storage")
    if storage_value is not None and not isinstance(storage_value, dict):
        raise ValueError("storage must be a mapping")
    storage = _merge_dict(paths, storage_value) if isinstance(storage_value, dict) else paths
    batch = result.get("batch") if isinstance(result.get("batch"), dict) else {}
    if result.get("batch") is not None and not isinstance(result.get("batch"), dict):
        raise ValueError("batch must be a mapping")
    storage_output_key = next((key for key in ("output_dir", "output") if key in storage), None)
    if storage_output_key is not None:
        # An explicitly supplied storage path is canonical and wins over the
        # historical batch.output_dir spelling.
        batch["output_dir"] = storage[storage_output_key]
    elif "output_dir" in batch:
        storage["output_dir"] = batch["output_dir"]
    data_key = next((key for key in ("data_dir", "data") if key in storage), None)
    if (
        data_key is not None
        and "sqlite_path" not in storage
        and isinstance(storage[data_key], (str, Path))
    ):
        storage["sqlite_path"] = Path(storage[data_key]) / "jobs.sqlite3"
    result["storage"] = storage
    result.pop("paths", None)
    result["batch"] = batch

    video = result.get("video")
    if (
        isinstance(video, dict)
        and "assets_dir" in video
        and not any(key in storage for key in ("assets_dir", "assets"))
    ):
        storage["assets_dir"] = video["assets_dir"]

    # Legacy ex_voice.wav_dir is the same runtime asset path used by storage.
    ex_voice = result.get("ex_voice")
    if isinstance(ex_voice, dict) and "wav_dir" in ex_voice and "exvoice_wav_dir" not in storage:
        storage["exvoice_wav_dir"] = ex_voice["wav_dir"]

    analysis = result.get("analysis", {})
    if isinstance(analysis, dict) and analysis.get("llm_policy") in ("off", False):
        # 読み上げoffで旧backendの資格情報検証を起動条件にしない。
        # news等の明示機能は自身のfactoryで必要なprovider設定を検証する。
        llm = result.get("llm")
        if isinstance(llm, dict):
            llm["enabled"] = False
    return result


__all__ = [
    "AnalysisSettings",
    "AppSettings",
    "ApplicationSettings",
    "BatchSettings",
    "ConfigPaths",
    "LLMBackendName",
    "LLMSettings",
    "PathSettings",
    "PathsSettings",
    "ProviderName",
    "ProviderSettings",
    "ProvidersSettings",
    "RuntimePaths",
    "SettingsPaths",
    "ServerSettings",
    "Settings",
    "StorageSettings",
    "TTSSettings",
]

# Descriptive names used by adapters and external callers.
AppSettings = Settings
ApplicationSettings = Settings
