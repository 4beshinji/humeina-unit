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

        provider: TTSProvider = VoisonaProvider(cfg)
    elif engine == "voicevox":
        from .voicevox import VoicevoxProvider

        provider = VoicevoxProvider(cfg)
    elif engine == "voicepeak":
        from .voicepeak import VoicepeakProvider

        provider = VoicepeakProvider(cfg)
    else:
        raise ValueError(f"Unknown TTS engine: {engine}")

    # 音源・engine更新の識別は利用者が確認した版を受け取る。
    # 不明な版のcacheを起動間で同一と扱わない方針はadapterに集約する。
    if cfg.get("cache_version"):
        provider.cache_version = str(cfg["cache_version"])
    return provider
