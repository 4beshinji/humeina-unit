"""全入口で共有する合成サービス。

Provider は一要求の合成だけを担当し、このサービスが voice 解決、retry、fallback、
cache、artifact 公開、timeout、concurrency、lifecycle を一元的に適用する。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import Utterance, VoiceIntent
from ..ports.artifacts import ArtifactRef, ArtifactStore
from ..ports.tts import (
    RETRYABLE_ERROR_CATEGORIES,
    ArtifactTarget,
    ProviderError,
    ProviderErrorCategory,
    ProviderHealth,
    ProviderOutputMode,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderVoice,
    SynthesisTarget,
    TTSProviderPort,
    classify_provider_error,
    normalize_health,
    normalize_voice_list,
)
from ..tts.audio_utils import validate_audio_bytes
from ..utils.atomic_io import atomic_replace_file, atomic_write_wav
from .operations import OperationDeadline
from .voice_parameters import VoiceResolutionContext, resolve_voice_parameters


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Provider呼び出しごとのretry/backoff設定。"""

    max_retries: int = 0
    backoff_factor: float = 0.0
    max_backoff: float = 30.0

    def __post_init__(self) -> None:
        if isinstance(self.max_retries, bool) or self.max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if self.backoff_factor < 0 or self.max_backoff < 0:
            raise ValueError("backoff values must be non-negative")

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    def delay(self, retry_index: int) -> float:
        return min(self.backoff_factor * (2**retry_index), self.max_backoff)


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    """Provider非依存の合成要求。"""

    text: str
    voice_intent: VoiceIntent = field(default_factory=VoiceIntent)
    utterance_id: str | None = None
    provider: str | None = None
    work_id: str | None = None
    run_id: str | None = None
    scene: str | None = None
    parameter_context: VoiceResolutionContext = field(default_factory=VoiceResolutionContext)
    destination: str | None = None
    target: SynthesisTarget | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("text must not be empty")
        if not isinstance(self.voice_intent, VoiceIntent):
            raise TypeError("voice_intent must be VoiceIntent")
        if not isinstance(self.parameter_context, VoiceResolutionContext):
            raise TypeError("parameter_context must be VoiceResolutionContext")
        if self.target is None and self.destination and self.work_id and self.run_id:
            object.__setattr__(
                self,
                "target",
                ArtifactTarget(
                    self.work_id,
                    self.run_id,
                    self.destination,
                    Path(self.destination).suffix.lstrip(".") or "wav",
                ),
            )
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_utterance(
        cls,
        utterance: Utterance,
        *,
        work_id: str | None = None,
        run_id: str | None = None,
        destination: str | None = None,
    ) -> "SynthesisRequest":
        return cls(
            text=utterance.text,
            voice_intent=utterance.voice_intent,
            utterance_id=utterance.id,
            work_id=work_id,
            run_id=run_id,
            scene=utterance.scene,
            destination=destination,
        )


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """bytes/file artifactを統一したapplication結果。"""

    audio_data: bytes | None
    provider: str
    status: str = "synthesized"
    format: str = "wav"
    sample_rate: int | None = None
    duration: float | None = None
    artifact: ArtifactRef | None = None
    output_mode: ProviderOutputMode = ProviderOutputMode.AUDIO_BYTES
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.audio_data is not None and not isinstance(self.audio_data, bytes):
            object.__setattr__(self, "audio_data", bytes(self.audio_data))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def audio(self) -> bytes | None:
        return self.audio_data

    @property
    def artifact_ref(self) -> ArtifactRef | None:
        return self.artifact

    @property
    def cache_hit(self) -> bool:
        return bool(self.metadata.get("cache_hit", False))

    @property
    def fallback_used(self) -> bool:
        return bool(self.metadata.get("fallback_used", False))

    @property
    def retry_count(self) -> int:
        return int(self.metadata.get("retry_count", 0))

    @property
    def is_success(self) -> bool:
        return self.status in {"synthesized", "fallback_synthesized"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "format": self.format,
            "sample_rate": self.sample_rate,
            "duration": self.duration,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "output_mode": self.output_mode.value,
            "metadata": dict(self.metadata),
        }


class SynthesisFailure(ProviderError):
    """全候補Providerが失敗した場合のapplication例外。"""

    def __init__(
        self,
        message: str,
        *,
        request: SynthesisRequest,
        category: ProviderErrorCategory,
        provider: str | None,
        attempts: int,
        metadata: Mapping[str, Any],
    ) -> None:
        super().__init__(message, category=category, provider=provider, details=metadata)
        self.request = request
        self.attempts = attempts
        self.metadata = dict(metadata)


class SynthesisService:
    """retry/fallback/cache/artifactを共有する唯一の合成ポリシー実装。"""

    def __init__(
        self,
        providers: Mapping[str, TTSProviderPort]
        | Sequence[TTSProviderPort]
        | TTSProviderPort
        | None = None,
        *,
        primary: str | TTSProviderPort | None = None,
        fallback: str | TTSProviderPort | None = None,
        retry_policy: RetryPolicy | Any | None = None,
        timeout: float | None = None,
        concurrency: int | Mapping[str, int] = 1,
        cache: Any | None = None,
        artifact_store: ArtifactStore | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._providers: dict[str, TTSProviderPort] = {}
        if providers is not None:
            if isinstance(providers, Mapping):
                for name, value in providers.items():
                    self._register(str(name), value)
            elif isinstance(providers, Sequence) and not isinstance(
                providers, (str, bytes, bytearray)
            ):
                for value in providers:
                    self._register(str(value.name), value)
            else:
                self._register(str(providers.name), providers)
        if primary is not None and not isinstance(primary, str):
            self._register(str(primary.name), primary)
        if fallback is not None and not isinstance(fallback, str):
            self._register(str(fallback.name), fallback)
        if not self._providers:
            raise ValueError("at least one TTS provider is required")
        self._primary = (
            self._provider_name(primary) if primary is not None else next(iter(self._providers))
        )
        self._fallback = self._provider_name(fallback) if fallback is not None else None
        if self._primary not in self._providers:
            raise ValueError(f"unknown primary provider: {self._primary}")
        if self._fallback is not None and self._fallback not in self._providers:
            raise ValueError(f"unknown fallback provider: {self._fallback}")
        if self._fallback == self._primary:
            raise ValueError("fallback must differ from primary")
        self.retry_policy = self._coerce_retry_policy(retry_policy)
        self.timeout = timeout
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        self._concurrency = concurrency
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self.cache = cache
        self.artifact_store = artifact_store
        # Keep this as a duck-typed port so application does not depend on the
        # HTTP/API metrics module.  Composition roots may provide any object
        # exposing ``record_synthesis``.
        self.metrics = metrics
        self._closed = False
        self._flight_registry_lock = asyncio.Lock()
        self._flight_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @property
    def primary_provider(self) -> TTSProviderPort:
        return self._providers[self._primary]

    @property
    def fallback_provider(self) -> TTSProviderPort | None:
        return self._providers.get(self._fallback) if self._fallback else None

    @property
    def providers(self) -> Mapping[str, TTSProviderPort]:
        return self._providers

    def _register(self, name: str, provider: TTSProviderPort) -> None:
        if not name.strip():
            raise ValueError("provider name must not be empty")
        self._providers[name] = provider

    def _provider_name(self, value: str | TTSProviderPort) -> str:
        return value if isinstance(value, str) else str(value.name)

    @staticmethod
    def _coerce_retry_policy(value: RetryPolicy | Any | None) -> RetryPolicy:
        if value is None:
            return RetryPolicy()
        if isinstance(value, RetryPolicy):
            return value
        return RetryPolicy(
            max_retries=int(getattr(value, "max_retries", 0)),
            backoff_factor=float(getattr(value, "backoff_factor", 0.0)),
            max_backoff=float(getattr(value, "max_backoff", 30.0)),
        )

    async def health(self, provider: str | TTSProviderPort | None = None) -> ProviderHealth:
        name = self._primary if provider is None else self._provider_name(provider)
        item = self._providers[name]
        started = time.monotonic()
        try:
            method = getattr(item, "health", None)
            value = await self._maybe_await(method() if callable(method) else method)
            result = normalize_health(value, provider=name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = ProviderHealth(False, provider=name, status="error", message=str(exc))
        if result.latency_ms is None:
            result = ProviderHealth(
                result.healthy,
                provider=result.provider or name,
                status=result.status,
                message=result.message,
                latency_ms=(time.monotonic() - started) * 1000,
                details=result.details,
            )
        return result

    async def list_voices(
        self, provider: str | TTSProviderPort | None = None
    ) -> list[ProviderVoice]:
        name = self._primary if provider is None else self._provider_name(provider)
        method = getattr(self._providers[name], "list_voices", None)
        if not callable(method):
            return []
        values = await self._maybe_await(method())
        return normalize_voice_list(values, provider=name)

    async def resolve_voice(
        self,
        intent: VoiceIntent,
        provider: str | TTSProviderPort,
    ) -> ProviderVoice | None:
        """intentを指定Provider向けに毎回解決する（fallbackで再利用しない）。"""

        name = self._provider_name(provider)
        item = self._providers[name]
        resolver = getattr(item, "resolve_voice", None)
        if callable(resolver):
            value = await self._maybe_await(resolver(intent))
            if value is not None:
                if isinstance(value, ProviderVoice):
                    return value
                if isinstance(value, Mapping):
                    return normalize_voice_list([value], provider=name)[0]
                return ProviderVoice(str(value), str(value), name)
        voices = await self.list_voices(name)
        if not voices:
            return None
        preferred = intent.preferred_voice_profile
        scored: list[tuple[int, int, ProviderVoice]] = []
        for index, voice in enumerate(voices):
            score = 0
            metadata = voice.metadata
            if preferred and preferred in {voice.id, voice.name, str(metadata.get("profile", ""))}:
                score += 100
            if intent.archetype and intent.archetype in {
                voice.id,
                voice.name,
                voice.provider,
                str(metadata.get("archetype", "")),
            }:
                score += 20
            if intent.gender and intent.gender == metadata.get("gender"):
                score += 5
            if intent.age_group and intent.age_group == metadata.get("age_group"):
                score += 5
            scored.append((-score, index, voice))
        scored.sort(key=lambda value: (value[0], value[1]))
        return scored[0][2]

    async def synthesize(
        self,
        request: SynthesisRequest | Utterance | str,
        *,
        timeout: float | None = None,
    ) -> SynthesisResult:
        """同一cache keyのmissを一つのprovider operationへ直列化する。"""

        req = self._normalize_request(request)
        if self.cache is None:
            return await self._synthesize_once(req, timeout=timeout)
        provider_name = req.provider or self._primary
        key = self._cache_key(provider_name, req)
        lock = await self._retain_flight_lock(key)
        try:
            async with lock:
                return await self._synthesize_once(req, timeout=timeout)
        finally:
            await self._release_flight_lock(key, lock)

    @staticmethod
    def _normalize_request(request: SynthesisRequest | Utterance | str) -> SynthesisRequest:
        if isinstance(request, SynthesisRequest):
            return request
        if isinstance(request, Utterance):
            return SynthesisRequest.from_utterance(request)
        return SynthesisRequest(str(request))

    async def _retain_flight_lock(self, key: str) -> asyncio.Lock:
        async with self._flight_registry_lock:
            existing = self._flight_locks.get(key)
            if existing is None:
                lock = asyncio.Lock()
                self._flight_locks[key] = (lock, 1)
                return lock
            lock, users = existing
            self._flight_locks[key] = (lock, users + 1)
            return lock

    async def _release_flight_lock(self, key: str, lock: asyncio.Lock) -> None:
        async with self._flight_registry_lock:
            existing = self._flight_locks.get(key)
            if existing is None or existing[0] is not lock:
                return
            users = existing[1] - 1
            if users <= 0:
                self._flight_locks.pop(key, None)
            else:
                self._flight_locks[key] = (lock, users)

    async def _synthesize_once(
        self,
        request: SynthesisRequest | Utterance | str,
        *,
        timeout: float | None = None,
    ) -> SynthesisResult:
        if self._closed:
            raise RuntimeError("SynthesisService is closed")
        req = self._normalize_request(request)
        if req.provider is not None:
            candidates = [req.provider]
        else:
            candidates = [self._primary] + ([self._fallback] if self._fallback else [])
        started = time.monotonic()
        operation_timeout = timeout if timeout is not None else self.timeout
        deadline = OperationDeadline.after(operation_timeout)
        errors: list[dict[str, Any]] = []
        total_attempts = 0
        total_retries = 0
        context = req.parameter_context
        resolved_parameters = resolve_voice_parameters(
            req.voice_intent,
            profile=context.profile,
            scene=context.scene,
            emotion=context.emotion,
            overrides=context.overrides,
        )
        for index, name in enumerate(candidates):
            provider = self._providers.get(name)
            if provider is None:
                continue
            fallback_used = index > 0
            key = self._cache_key(
                name,
                req,
                resolved_parameters=resolved_parameters,
            )
            try:
                cached = await deadline.run(self._cache_get(key, name, req))
            except TimeoutError:
                errors.append(self._deadline_error(name))
                break
            if cached is not None:
                result = self._result_from_value(cached, provider=name)
                try:
                    result = await deadline.run(
                        self._finish_result(
                            result,
                            req,
                            provider=name,
                            fallback_used=fallback_used,
                            cache_hit=True,
                            retry_count=0,
                            attempts=0,
                            started=started,
                        )
                    )
                except TimeoutError:
                    errors.append(self._deadline_error(name))
                    break
                self._record_metrics(
                    duration_ms=(time.monotonic() - started) * 1000,
                    provider=name,
                    cache_hit=True,
                    fallback_used=fallback_used,
                    text_length=len(req.text),
                )
                return result
            try:
                health = await deadline.run(self.health(name))
            except TimeoutError:
                errors.append(self._deadline_error(name))
                break
            if not health.healthy:
                errors.append(
                    {
                        "provider": name,
                        "category": "unavailable",
                        "message": health.message or health.status,
                    }
                )
                continue
            try:
                voice = await deadline.run(self.resolve_voice(req.voice_intent, name))
            except TimeoutError:
                errors.append(self._deadline_error(name))
                break
            provider_metadata = dict(req.metadata)
            if fallback_used:
                # IDs from the primary provider are not portable.  Fallback adapters must
                # resolve their own concrete voice from the provider-independent intent.
                for key in ("voice_id", "speaker_id", "voice", "default_voice"):
                    provider_metadata.pop(key, None)
            provider_request = ProviderSynthesisRequest(
                text=req.text,
                voice_intent=req.voice_intent,
                voice=voice,
                utterance_id=req.utterance_id,
                destination=req.destination,
                target=req.target,
                resolved_parameters=resolved_parameters,
                metadata=provider_metadata,
            )
            try:
                raw, attempts, retry_errors = await self._call_provider(
                    provider,
                    provider_request,
                    deadline=deadline,
                )
                total_attempts += attempts
                total_retries += len(retry_errors)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                errors.append(self._deadline_error(name))
                break
            except ProviderError as exc:
                total_attempts += int(getattr(exc, "attempts", 0) or 0)
                total_retries += int(getattr(exc, "retry_count", 0) or 0)
                errors.append(
                    {"provider": name, "category": exc.category.value, "message": exc.message}
                )
                if deadline.expired():
                    errors[-1] = self._deadline_error(name)
                    break
                continue
            result = self._result_from_value(raw, provider=name)
            try:
                result = await deadline.run(
                    self._finish_result(
                        result,
                        req,
                        provider=name,
                        fallback_used=fallback_used,
                        cache_hit=False,
                        retry_count=total_retries,
                        attempts=attempts,
                        started=started,
                    )
                )
            except TimeoutError:
                errors.append(self._deadline_error(name))
                break
            except ProviderError as exc:
                errors.append(
                    {"provider": name, "category": exc.category.value, "message": exc.message}
                )
                continue
            if self.cache is not None and result.audio_data:
                try:
                    await deadline.run(self._cache_put(key, result.audio_data, name, req))
                except TimeoutError:
                    errors.append(self._deadline_error(name))
                    break
            self._record_metrics(
                duration_ms=(time.monotonic() - started) * 1000,
                provider=name,
                cache_hit=False,
                fallback_used=fallback_used,
                text_length=len(req.text),
            )
            return result
        category = ProviderErrorCategory.UNKNOWN
        provider_name = candidates[-1] if candidates else None
        if errors:
            try:
                category = ProviderErrorCategory(errors[-1]["category"])
            except ValueError:
                category = ProviderErrorCategory.UNKNOWN
        metadata = {
            "provider": provider_name,
            "primary_provider": self._primary,
            "fallback_used": bool(len(candidates) > 1 and errors),
            "cache_hit": False,
            "attempts": total_attempts,
            "retry_count": total_retries,
            "errors": errors,
            "elapsed_ms": (time.monotonic() - started) * 1000,
        }
        self._record_metrics(
            duration_ms=(time.monotonic() - started) * 1000,
            provider=provider_name,
            error=True,
            error_category=category.value,
            fallback_used=bool(len(candidates) > 1 and errors),
            text_length=len(req.text),
        )
        raise SynthesisFailure(
            "all synthesis providers failed",
            request=req,
            category=category,
            provider=provider_name,
            attempts=total_attempts,
            metadata=metadata,
        )

    async def _call_provider(
        self,
        provider: TTSProviderPort,
        request: ProviderSynthesisRequest,
        *,
        deadline: OperationDeadline,
    ) -> tuple[Any, int, list[ProviderError]]:
        name = str(provider.name)
        errors: list[ProviderError] = []
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                semaphore = self._semaphore(name)
                await deadline.run(semaphore.acquire())
                try:
                    call = provider.synthesize(request)
                    raw = await deadline.run(call)
                finally:
                    semaphore.release()
                return raw, attempt, errors
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                classified = classify_provider_error(exc, provider=name)
                errors.append(classified)
                if (
                    attempt < self.retry_policy.max_attempts
                    and classified.category in RETRYABLE_ERROR_CATEGORIES
                    and classified.retryable
                ):
                    delay = self.retry_policy.delay(attempt - 1)
                    if delay:
                        await deadline.sleep(delay)
                    continue
                classified.attempts = attempt
                classified.retry_count = max(0, len(errors) - 1)
                raise classified from exc
        raise RuntimeError("retry loop exhausted")

    @staticmethod
    def _deadline_error(provider: str) -> dict[str, str]:
        return {
            "provider": provider,
            "category": ProviderErrorCategory.TIMEOUT.value,
            "message": "operation deadline exceeded",
        }

    def _semaphore(self, name: str) -> asyncio.Semaphore:
        if name not in self._semaphores:
            limit = (
                self._concurrency.get(name, 1)
                if isinstance(self._concurrency, Mapping)
                else self._concurrency
            )
            if isinstance(limit, bool) or int(limit) < 1:
                raise ValueError("provider concurrency must be >= 1")
            self._semaphores[name] = asyncio.Semaphore(int(limit))
        return self._semaphores[name]

    def _result_from_value(self, raw: Any, *, provider: str) -> ProviderSynthesisResult:
        if isinstance(raw, ProviderSynthesisResult):
            return raw
        if isinstance(raw, (bytes, bytearray, memoryview)):
            return ProviderSynthesisResult(bytes(raw))
        if isinstance(raw, ArtifactRef):
            return ProviderSynthesisResult(artifact=raw)
        if isinstance(raw, Mapping):
            artifact = raw.get("artifact", raw.get("artifact_ref"))
            if isinstance(artifact, Mapping):
                try:
                    artifact = ArtifactRef(
                        artifact_id=str(artifact.get("artifact_id", artifact.get("id", ""))),
                        relative_path=str(artifact.get("relative_path", artifact.get("path", ""))),
                        path=Path(str(artifact.get("path", artifact.get("relative_path", "")))),
                        checksum=str(artifact.get("checksum", artifact.get("sha256", ""))),
                        size_bytes=int(artifact.get("size_bytes", 0)),
                    )
                except (TypeError, ValueError):
                    artifact = None
            return ProviderSynthesisResult(
                audio_data=raw.get("audio_data", raw.get("audio", raw.get("data"))),
                format=str(raw.get("format", "wav")),
                sample_rate=raw.get("sample_rate"),
                duration=raw.get("duration"),
                artifact=artifact if isinstance(artifact, ArtifactRef) else None,
                metadata={
                    k: v
                    for k, v in raw.items()
                    if k
                    not in {
                        "audio_data",
                        "audio",
                        "data",
                        "format",
                        "sample_rate",
                        "duration",
                        "artifact",
                        "artifact_ref",
                    }
                },
            )
        raise ProviderError(
            f"provider {provider} returned unsupported result",
            category=ProviderErrorCategory.INVALID_RESPONSE,
            provider=provider,
            retryable=False,
        )

    async def _finish_result(
        self,
        result: ProviderSynthesisResult,
        request: SynthesisRequest,
        *,
        provider: str,
        fallback_used: bool,
        cache_hit: bool,
        retry_count: int,
        attempts: int,
        started: float,
    ) -> SynthesisResult:
        data = result.audio_data
        artifact = result.artifact
        file_path = result.metadata.get("file_path")
        if file_path:
            path = Path(str(file_path))
            if data is None and path.is_file():
                data = path.read_bytes()
            if result.metadata.get("_cleanup_file"):
                path.unlink(missing_ok=True)
        validation_data = data
        if validation_data is None and artifact is not None and artifact.path.is_file():
            validation_data = artifact.path.read_bytes()
        if validation_data:
            try:
                descriptor = validate_audio_bytes(validation_data, result.format)
                if (
                    result.sample_rate is not None
                    and descriptor.sample_rate is not None
                    and int(result.sample_rate) != descriptor.sample_rate
                ):
                    raise ValueError("declared sample rate does not match audio bytes")
            except (OSError, ValueError) as exc:
                raise ProviderError(
                    "provider returned invalid audio",
                    category=ProviderErrorCategory.INVALID_RESPONSE,
                    provider=provider,
                    retryable=False,
                    details={"reason": str(exc), "format": result.format},
                ) from exc
        if not data and artifact is None:
            if result.output_mode is ProviderOutputMode.REMOTE_PLAYBACK:
                data = None
            else:
                raise ProviderError(
                    "provider returned empty audio without an artifact",
                    category=ProviderErrorCategory.INVALID_RESPONSE,
                    provider=provider,
                    retryable=False,
                )
        if request.destination:
            artifact = await self._publish_artifact(
                data,
                artifact,
                request,
                provider=provider,
                duration=result.duration,
            )
        metadata = dict(result.metadata)
        metadata.pop("_cleanup_file", None)
        if file_path:
            metadata.pop("file_path", None)
        metadata.update(
            {
                "provider": provider,
                "primary_provider": self._primary,
                "fallback_used": fallback_used,
                "cache_hit": cache_hit,
                "retry_count": retry_count,
                "attempts": attempts,
                "elapsed_ms": (time.monotonic() - started) * 1000,
            }
        )
        return SynthesisResult(
            audio_data=data,
            provider=provider,
            status="fallback_synthesized" if fallback_used else "synthesized",
            format=result.format,
            sample_rate=result.sample_rate,
            duration=result.duration,
            artifact=artifact,
            output_mode=result.output_mode,
            metadata=metadata,
        )

    async def _publish_artifact(
        self,
        data: bytes | None,
        artifact: ArtifactRef | None,
        request: SynthesisRequest,
        *,
        provider: str,
        duration: float | None,
    ) -> ArtifactRef | None:
        if self.artifact_store is None:
            path = Path(request.destination or "")
            if data is not None:
                if path.suffix.lower() == ".wav":
                    atomic_write_wav(path, data)
                else:
                    def write_audio(temporary: Path) -> None:
                        temporary.write_bytes(data)

                    atomic_replace_file(path, write_audio)
            return artifact
        store = self.artifact_store
        destination = request.destination
        if artifact is not None and data is None:
            source = artifact.path
        else:
            if data is None:
                return artifact
            import tempfile

            with tempfile.NamedTemporaryFile(
                suffix=Path(destination or "audio.wav").suffix, delete=False
            ) as temp:
                temp.write(data)
                source = Path(temp.name)
        try:
            kwargs = {
                "artifact_id": request.utterance_id,
                "provider": provider,
                "duration": duration,
            }
            if request.work_id is not None and request.run_id is not None:
                kwargs.update({"work_id": request.work_id, "run_id": request.run_id})
            try:
                published = store.publish(source, destination, **kwargs)
            except TypeError:
                published = store.publish(source, destination)
            recorder = getattr(store, "record_utterance", None)
            if callable(recorder) and request.work_id and request.run_id and request.utterance_id:
                try:
                    recorder(
                        request.work_id,
                        request.run_id,
                        request.utterance_id,
                        status="synthesized",
                        artifact=published,
                        provider=provider,
                        duration=duration,
                    )
                except TypeError:
                    recorder(
                        request.work_id, request.run_id, request.utterance_id, artifact=published
                    )
            return published
        finally:
            if source != (artifact.path if artifact else None):
                source.unlink(missing_ok=True)

    def _cache_key(
        self,
        provider: str,
        request: SynthesisRequest,
        *,
        resolved_parameters: Any | None = None,
    ) -> str:
        item = self._providers.get(provider)
        provider_version = getattr(item, "cache_version", getattr(item, "version", None))
        target_format = getattr(request.target, "format", None) or request.metadata.get(
            "format", "wav"
        )
        resolved = resolved_parameters or resolve_voice_parameters(
            request.voice_intent,
            profile=request.parameter_context.profile,
            scene=request.parameter_context.scene,
            emotion=request.parameter_context.emotion,
            overrides=request.parameter_context.overrides,
        )
        payload = {
            "schema_version": 2,
            "provider": provider,
            "provider_version": provider_version,
            "format": str(target_format).lower().lstrip("."),
            "text": request.text,
            "voice_intent": request.voice_intent.to_dict(),
            "resolved_parameters": asdict(resolved),
            "parameter_context": asdict(request.parameter_context),
            "metadata": dict(request.metadata),
            "target": asdict(request.target) if request.target is not None else None,
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    async def _cache_get(self, key: str, provider: str, request: SynthesisRequest) -> Any | None:
        if self.cache is None:
            return None
        canonical = getattr(self.cache, "get_key", None)
        if callable(canonical):
            return await self._maybe_await(canonical(key))
        if not callable(getattr(self.cache, "get", None)):
            return None
        method = self.cache.get
        try:
            parameters = list(inspect.signature(method).parameters)
        except (TypeError, ValueError):
            parameters = []
        if len(parameters) >= 2:
            value = method(provider, request.text, **dict(request.metadata))
        else:
            value = method(key)
        return await self._maybe_await(value)

    async def _cache_put(
        self, key: str, data: bytes, provider: str, request: SynthesisRequest
    ) -> None:
        canonical = getattr(self.cache, "put_key", None)
        if callable(canonical):
            await self._maybe_await(canonical(key, data))
            return
        method = getattr(self.cache, "put", None)
        if not callable(method):
            return
        try:
            parameters = list(inspect.signature(method).parameters)
        except (TypeError, ValueError):
            parameters = []
        if len(parameters) >= 3:
            value = method(data, provider, request.text, **dict(request.metadata))
        else:
            value = method(key, data)
        await self._maybe_await(value)

    def _record_metrics(
        self,
        *,
        duration_ms: float,
        provider: str | None = None,
        error: bool = False,
        error_category: str | None = None,
        cache_hit: bool = False,
        fallback_used: bool = False,
        partial: bool = False,
        text_length: int | None = None,
    ) -> None:
        """Emit one logical synthesis event through an optional metrics port."""

        recorder = getattr(self.metrics, "record_synthesis", None)
        if not callable(recorder):
            return
        kwargs = {
            "duration_ms": duration_ms,
            "error": error,
            "cache_hit": cache_hit,
            "provider": provider,
            "error_category": error_category,
            "fallback_used": fallback_used,
            "partial": partial,
            "text_length": text_length,
        }
        try:
            recorder(**kwargs)
        except TypeError:
            # Accommodate a small pre-OBS metrics fake while never forwarding
            # synthesis text to a metrics implementation.
            recorder(
                duration_ms=duration_ms,
                error=error,
                cache_hit=cache_hit,
                text="",
            )

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        seen: set[int] = set()
        for provider in self._providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            method = getattr(provider, "close", None)
            if not callable(method):
                continue
            try:
                await self._maybe_await(method())
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def __aenter__(self) -> "SynthesisService":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()


__all__ = [
    "RetryPolicy",
    "SynthesisFailure",
    "SynthesisRequest",
    "SynthesisResult",
    "SynthesisService",
]
