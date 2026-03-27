"""TTS provider factory — extracted from cli.py for reuse."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TTSProvider

if TYPE_CHECKING:
    from ..api.config import TTSEngineConfig


def create_provider(config: TTSEngineConfig) -> TTSProvider:
    """TTSEngineConfigからプロバイダーインスタンスを生成."""
    return create_provider_from_dict(config.engine, config.to_provider_dict())


def create_provider_from_dict(engine: str, cfg: dict) -> TTSProvider:
    """エンジン名とdict設定からプロバイダーインスタンスを生成."""
    if engine == "voisona":
        from .voisona import VoisonaProvider

        return VoisonaProvider(cfg)
    elif engine == "voicevox":
        from .voicevox import VoicevoxProvider

        return VoicevoxProvider(cfg)
    elif engine == "voicepeak":
        from .voicepeak import VoicepeakProvider

        return VoicepeakProvider(cfg)
    else:
        raise ValueError(f"Unknown TTS engine: {engine}")
