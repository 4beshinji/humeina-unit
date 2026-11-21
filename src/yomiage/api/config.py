"""Programmatic configuration models for the public API."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..config import load_config
from ..settings import (
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


class TTSEngineConfig(BaseModel):
    """TTSエンジン設定."""

    engine: str  # "voicevox" | "voisona" | "voicepeak"
    url: str | None = None
    username: str | None = None
    password: str | None = None
    default_voice: str | int | None = None
    extra: dict = Field(default_factory=dict)

    def to_provider_dict(self) -> dict:
        """プロバイダーが期待するdict形式に変換."""
        d: dict = {}
        if self.url is not None:
            d["url"] = self.url
        if self.username is not None:
            d["username"] = self.username
        if self.password is not None:
            d["password"] = self.password
        if self.default_voice is not None:
            if self.engine == "voicevox":
                d["default_speaker"] = self.default_voice
            elif self.engine == "voicepeak":
                d["default_narrator"] = self.default_voice
            else:
                d["default_voice"] = self.default_voice
        d.update(self.extra)
        return d


class LLMConfig(BaseModel):
    """LLMバックエンド設定."""

    backend: str = "opencode_go"
    url: str | None = "https://opencode.ai/zen/go/v1"
    api_key: str | None = None
    model: str | None = "qwen3.8-flash"
    protocol: str | None = "auto"


class AnalyzerConfig(BaseModel):
    """テキスト分析設定."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    max_chunk_chars: int = 200


class PipelineConfig(BaseModel):
    """統合パイプライン設定."""

    tts: TTSEngineConfig
    tts_fallback: TTSEngineConfig | None = None
    analyzer: AnalyzerConfig = Field(default_factory=AnalyzerConfig)
    scene_params: dict | None = None
    lookahead: int = 3
    voice_profile_dir: Path | None = None
    voice_profile_name: str | None = None

    @classmethod
    def from_yaml(cls, config_dir: str | Path | None = None) -> PipelineConfig:
        """既存YAML設定ディレクトリから読み込み."""
        raw = load_config(Path(config_dir) if config_dir else None)
        return cls.from_dict(raw)

    @classmethod
    def from_settings(cls, settings: Settings) -> PipelineConfig:
        """型付きSettingsから、既存SDK DTOへ最小変換する。

        Provider実装の全面移行（SYN-001）はCFG-001の対象外なので、変換は
        ここに閉じ込めてraw dictが新しいapplication層へ漏れないようにする。
        """

        if not isinstance(settings, Settings):
            raise TypeError("settings must be a Settings instance")
        return cls.from_dict(settings.to_legacy_dict())

    @classmethod
    def from_dict(cls, data: dict) -> PipelineConfig:
        """既存のraw dict設定形式から変換."""
        tts_cfg = data.get("tts", {})
        primary_name = tts_cfg.get("primary_provider", "voicevox")
        fallback_name = tts_cfg.get("fallback_provider")

        supported = {"voisona", "voicevox", "voicepeak"}
        configured_names = (primary_name, fallback_name)
        unknown = [
            name
            for name in configured_names
            if name is not None and (not isinstance(name, str) or name not in supported)
        ]
        if unknown:
            raise ValueError(f"Unknown TTS provider: {', '.join(map(str, unknown))}")

        primary_engine_cfg = data.get(primary_name, {})
        tts = cls._provider_config(primary_name, primary_engine_cfg)

        tts_fallback = None
        if fallback_name and fallback_name != primary_name:
            fb_cfg = data.get(fallback_name, {})
            tts_fallback = cls._provider_config(fallback_name, fb_cfg)

        legacy_ollama = data.get("ollama")
        if isinstance(legacy_ollama, dict):
            llm = LLMConfig(
                backend="ollama",
                url=legacy_ollama.get("url"),
                model=legacy_ollama.get("model") or "qwen3.5:9b",
                protocol="ollama_chat",
            )
        else:
            llm_section = data.get("llm", {})
            providers = llm_section.get("providers", {}) if isinstance(llm_section, dict) else {}
            routes = llm_section.get("routes", {}) if isinstance(llm_section, dict) else {}
            scene_route = routes.get("scene_analysis") or routes.get("default") or {}
            provider_name = scene_route.get("provider") if isinstance(scene_route, dict) else None
            if not provider_name and isinstance(providers, dict):
                provider_name = next(
                    (
                        name
                        for name, values in providers.items()
                        if isinstance(values, dict) and values.get("enabled", True)
                    ),
                    None,
                )
            selected = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
            if provider_name and not selected:
                raise ValueError(f"Unknown LLM provider: {provider_name}")
            if selected.get("enabled", True) is False:
                raise ValueError(f"LLM provider '{provider_name}' is disabled")
            llm = (
                LLMConfig(
                    backend=selected.get("type", str(provider_name)),
                    url=selected.get("base_url"),
                    api_key=selected.get("api_key") or None,
                    model=selected.get("model"),
                    protocol=selected.get("protocol"),
                )
                if provider_name
                else LLMConfig()
            )
        analyzer = AnalyzerConfig(
            llm=llm,
            max_chunk_chars=tts_cfg.get("max_chunk_chars", 200),
        )

        batch_cfg = data.get("batch", {})
        return cls(
            tts=tts,
            tts_fallback=tts_fallback,
            analyzer=analyzer,
            scene_params=data.get("scene_params"),
            lookahead=tts_cfg.get("lookahead_chunks", 3),
            voice_profile_dir=Path(batch_cfg["voice_profile_dir"])
            .expanduser()
            if batch_cfg.get("voice_profile_dir")
            else None,
            voice_profile_name=batch_cfg.get("default_voice_profile"),
        )

    @staticmethod
    def _provider_config(engine: str, raw: dict) -> TTSEngineConfig:
        if not isinstance(raw, dict):
            raise TypeError(f"{engine} provider settings must be a mapping")
        reserved = {
            "url",
            "username",
            "password",
            "default_voice",
            "default_speaker",
            "default_narrator",
        }
        default_voice = next(
            (
                raw[key]
                for key in ("default_voice", "default_speaker", "default_narrator")
                if key in raw and raw[key] is not None
            ),
            None,
        )
        return TTSEngineConfig(
            engine=engine,
            url=raw.get("url"),
            username=raw.get("username"),
            password=raw.get("password"),
            default_voice=default_voice,
            extra={key: value for key, value in raw.items() if key not in reserved},
        )


__all__ = [
    "AppSettings",
    "ApplicationSettings",
    "AnalyzerConfig",
    "BatchSettings",
    "ConfigPaths",
    "LLMConfig",
    "LLMSettings",
    "PathSettings",
    "PathsSettings",
    "PipelineConfig",
    "ProviderSettings",
    "ProvidersSettings",
    "RuntimePaths",
    "SettingsPaths",
    "ServerSettings",
    "Settings",
    "StorageSettings",
    "TTSSettings",
    "TTSEngineConfig",
]
