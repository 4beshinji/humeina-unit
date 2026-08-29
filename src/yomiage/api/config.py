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
        if self.url:
            d["url"] = self.url
        if self.username:
            d["username"] = self.username
        if self.password:
            d["password"] = self.password
        if self.default_voice:
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

    backend: str = "llama_cpp"
    url: str | None = None
    api_key: str | None = None
    model: str | None = "Qwen3.8-27B"
    protocol: str | None = "chat_completions"


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
        tts = TTSEngineConfig(
            engine=primary_name,
            url=primary_engine_cfg.get("url"),
            username=primary_engine_cfg.get("username"),
            password=primary_engine_cfg.get("password"),
            default_voice=(
                primary_engine_cfg.get("default_voice")
                or primary_engine_cfg.get("default_speaker")
                or primary_engine_cfg.get("default_narrator")
            ),
        )

        tts_fallback = None
        if fallback_name and fallback_name != primary_name:
            fb_cfg = data.get(fallback_name, {})
            tts_fallback = TTSEngineConfig(
                engine=fallback_name,
                url=fb_cfg.get("url"),
                username=fb_cfg.get("username"),
                password=fb_cfg.get("password"),
                default_voice=(
                    fb_cfg.get("default_voice")
                    or fb_cfg.get("default_speaker")
                    or fb_cfg.get("default_narrator")
                ),
            )

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
            local = providers.get("local", {}) if isinstance(providers, dict) else {}
            llm = LLMConfig(
                backend=local.get("type", "llama_cpp"),
                url=local.get("base_url", "http://127.0.0.1:8080/v1"),
                model=local.get("model", "Qwen3.8-27B"),
                protocol=local.get("protocol", "chat_completions"),
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
