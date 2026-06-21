"""Programmatic configuration models for the public API."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..config import load_config


class TTSEngineConfig(BaseModel):
    """TTSエンジン設定."""

    engine: str  # "voicevox" | "voisona" | "voicepeak"
    url: str | None = None
    username: str | None = None
    password: str | None = None
    default_voice: str | None = None
    extra: dict = {}

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

    backend: str = "ollama"  # "ollama" | "openai" | "anthropic"
    url: str | None = None
    api_key: str | None = None
    model: str | None = None


class AnalyzerConfig(BaseModel):
    """テキスト分析設定."""

    llm: LLMConfig = LLMConfig()
    max_chunk_chars: int = 200


class PipelineConfig(BaseModel):
    """統合パイプライン設定."""

    tts: TTSEngineConfig
    tts_fallback: TTSEngineConfig | None = None
    analyzer: AnalyzerConfig = AnalyzerConfig()
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
    def from_dict(cls, data: dict) -> PipelineConfig:
        """既存のraw dict設定形式から変換."""
        tts_cfg = data.get("tts", {})
        primary_name = tts_cfg.get("primary_provider", "voicevox")
        fallback_name = tts_cfg.get("fallback_provider")

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

        ollama_cfg = data.get("ollama", {})
        llm = LLMConfig(
            backend="ollama",
            url=ollama_cfg.get("url"),
            model=ollama_cfg.get("model"),
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
            if batch_cfg.get("voice_profile_dir")
            else None,
            voice_profile_name=batch_cfg.get("default_voice_profile"),
        )
