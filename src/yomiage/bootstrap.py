"""Composition root helpers for TTS providers and application services.

Interfaces such as the public SDK and Studio intentionally depend on these helpers rather
than importing a concrete provider implementation.  Keeping construction here also gives
callers one place to apply the same adapter and synthesis policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .adapters.tts import LegacyTTSProviderAdapter, VoisonaFileProviderAdapter
from .api.hooks import EventHooks
from .api.metrics import MetricsCollector
from .api.profile_resolver import resolve_voice_profile
from .application.synthesis import RetryPolicy, SynthesisService
from .config import get_tts_config
from .nlp.ollama_client import OllamaClient
from .nlp.scene_analyzer import SceneAnalyzer
from .nlp.splitter import TextSplitter
from .reader.engine import ReadingEngine
from .reader.param_mapper import ParamMapper
from .tts.base import TTSProvider
from .tts.cache import TTSCache
from .tts.factory import create_provider_from_dict
from .tts.manager import TTSManager


def create_tts_provider(config: Mapping[str, Any] | Any, provider: str | None = None) -> Any:
    """設定からlegacy TTS providerを生成するcomposition-root helper。

    Providerの具象constructorは ``tts.factory`` に閉じ込める。``TTSEngineConfig`` を
    直接渡す公開SDK呼び出しもここで受けることで、CLI/Batch/Bridgeがそれぞれ別の
    constructor mapを持たないようにする。
    """

    if provider is None:
        engine = getattr(config, "engine", None)
        if not isinstance(engine, str) or not engine:
            raise ValueError("provider name is required")
        provider = engine
        to_dict = getattr(config, "to_provider_dict", None)
        values = to_dict() if callable(to_dict) else get_tts_config(config, provider)
    else:
        values = get_tts_config(config, provider)
    return create_provider_from_dict(str(provider), dict(values))


def create_tts_manager(
    config: Mapping[str, Any] | Any,
    provider_override: str | None = None,
    *,
    voice_profile: Any | None = None,
    hooks: EventHooks | None = None,
    metrics: MetricsCollector | None = None,
) -> TTSManager:
    """Reader/News互換のTTSManager facadeをcomposition rootで生成する。"""

    raw = config if isinstance(config, Mapping) else {}
    tts_cfg = raw.get("tts", {}) if isinstance(raw, Mapping) else {}
    if not isinstance(tts_cfg, Mapping):
        tts_cfg = {}
    primary_name = str(provider_override or tts_cfg.get("primary_provider", "voicevox"))
    fallback_name = tts_cfg.get("fallback_provider")
    primary = create_tts_provider(config, primary_name)
    fallback = None
    if fallback_name and str(fallback_name) != primary_name:
        fallback = create_tts_provider(config, str(fallback_name))

    providers = {primary.name: primary}
    if fallback is not None:
        providers[fallback.name] = fallback
    service = create_synthesis_service(
        providers,
        primary=primary.name,
        fallback=fallback.name if fallback is not None else None,
        config=raw,
        metrics=metrics,
    )
    return TTSManager(
        primary=primary,
        fallback=fallback,
        lookahead=int(tts_cfg.get("lookahead_chunks", 3)),
        voice_profile=voice_profile,
        hooks=hooks,
        metrics=metrics,
        service=service,
        owns_service=True,
    )


def create_reading_engine(
    config: Mapping[str, Any],
    *,
    hooks: EventHooks | None = None,
    metrics: MetricsCollector | None = None,
) -> ReadingEngine:
    """Construct the Reader composition at the application bootstrap boundary."""

    tts_cfg = config.get("tts", {})
    if not isinstance(tts_cfg, Mapping):
        tts_cfg = {}
    voice_section = config.get("voisona", {})
    voice_name = voice_section.get("default_voice") if isinstance(voice_section, Mapping) else None
    voice_profile = resolve_voice_profile(voice_name) if voice_name else None
    tts_manager = create_tts_manager(
        config,
        voice_profile=voice_profile,
        hooks=hooks,
        metrics=metrics,
    )
    ollama_cfg = config.get("ollama", {})
    if not isinstance(ollama_cfg, Mapping):
        ollama_cfg = {}
    ollama = OllamaClient(
        url=str(ollama_cfg.get("url", "http://localhost:11434")),
        model=str(ollama_cfg.get("model", "qwen3.5:9b")),
    )
    scene_params = config.get("scene_params")
    param_mapper = (
        ParamMapper(dict(scene_params))
        if isinstance(scene_params, Mapping)
        else ParamMapper.from_config_file()
    )
    reader_cfg = config.get("reader", {})
    if not isinstance(reader_cfg, Mapping):
        reader_cfg = {}
    return ReadingEngine(
        tts_manager=tts_manager,
        splitter=TextSplitter(max_chars=int(tts_cfg.get("max_chunk_chars", 200))),
        scene_analyzer=SceneAnalyzer(ollama),
        param_mapper=param_mapper,
        auto_advance=bool(reader_cfg.get("auto_advance", True)),
        lookahead_chunks=int(tts_cfg.get("lookahead_chunks", 5)),
        synthesis_service=tts_manager.service,
        metrics=metrics,
    )


def create_studio_providers(
    config: Mapping[str, Any] | Any,
    provider_names: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Studioで必要なproviderだけを設定から生成する。"""

    providers: dict[str, Any] = {}
    for name in provider_names:
        try:
            providers[name] = create_tts_provider(config, name)
        except ValueError:
            # Unknown provider names are handled by the caller as before.  Do not turn a
            # configuration typo into an import-time failure for unrelated Studio projects.
            continue
    return providers


def create_synthesis_service(
    providers: Mapping[str, Any],
    *,
    primary: str,
    fallback: str | None = None,
    cache: TTSCache | Any | None = None,
    artifact_store: Any | None = None,
    config: Mapping[str, Any] | Any | None = None,
    retry_policy: RetryPolicy | Any | None = None,
    timeout: float | None = None,
    concurrency: int | Mapping[str, int] | None = None,
    metrics: Any | None = None,
) -> SynthesisService:
    """legacy provider群を統一SynthesisServiceへ適合する。"""

    adapted: dict[str, Any] = {}
    for name, provider in providers.items():
        if not isinstance(provider, TTSProvider) and hasattr(provider, "health") and hasattr(
            provider, "synthesize"
        ):
            # Already-adapted fakes/ports are accepted for SDK and tests.
            adapted[str(name)] = provider
        elif str(name) == "voisona":
            adapted[str(name)] = VoisonaFileProviderAdapter(provider, name=str(name))
        else:
            adapted[str(name)] = LegacyTTSProviderAdapter(provider, name=str(name))

    if primary not in adapted:
        raise ValueError(f"Provider not found: {primary}")

    resolved_retry_policy = retry_policy
    resolved_timeout = timeout
    resolved_concurrency: int | Mapping[str, int] = concurrency or 1
    if config is not None:
        raw = config if isinstance(config, Mapping) else {}
        synthesis_cfg = raw.get("synthesis", {})
        batch_cfg = raw.get("batch", {})
        if not isinstance(synthesis_cfg, Mapping):
            synthesis_cfg = {}
        if not isinstance(batch_cfg, Mapping):
            batch_cfg = {}
        retry_cfg = synthesis_cfg.get("retry", batch_cfg.get("retry", {}))
        if isinstance(retry_cfg, Mapping):
            resolved_retry_policy = RetryPolicy(
                max_retries=int(retry_cfg.get("max_retries", 0)),
                backoff_factor=float(retry_cfg.get("backoff_factor", 0.0)),
                max_backoff=float(retry_cfg.get("max_backoff", 30.0)),
            )
        timeout_value = synthesis_cfg.get("timeout", batch_cfg.get("synthesis_timeout"))
        if resolved_timeout is None:
            resolved_timeout = float(timeout_value) if timeout_value is not None else None
        resolved_concurrency = synthesis_cfg.get(
            "concurrency", batch_cfg.get("provider_concurrency", 1)
        )

    return SynthesisService(
        adapted,
        primary=primary,
        fallback=fallback,
        retry_policy=resolved_retry_policy,
        timeout=resolved_timeout,
        concurrency=resolved_concurrency,
        cache=cache,
        artifact_store=artifact_store,
        metrics=metrics,
    )


__all__ = [
    "create_reading_engine",
    "create_synthesis_service",
    "create_studio_providers",
    "create_tts_manager",
    "create_tts_provider",
]
