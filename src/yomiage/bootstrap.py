"""Composition root helpers for TTS providers and application services.

Interfaces such as the public SDK and Studio intentionally depend on these helpers rather
than importing a concrete provider implementation.  Keeping construction here also gives
callers one place to apply the same adapter and synthesis policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .adapters.tts import LegacyTTSProviderAdapter, VoisonaFileProviderAdapter
from .application.synthesis import RetryPolicy, SynthesisService
from .config import get_tts_config
from .tts.base import TTSProvider
from .tts.cache import TTSCache
from .tts.factory import create_provider_from_dict


def create_tts_provider(config: Mapping[str, Any] | Any, provider: str) -> Any:
    """設定からlegacy TTS providerを生成するcomposition-root helper。"""

    return create_provider_from_dict(provider, dict(get_tts_config(config, provider)))


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
    )


__all__ = [
    "create_synthesis_service",
    "create_studio_providers",
    "create_tts_provider",
]
