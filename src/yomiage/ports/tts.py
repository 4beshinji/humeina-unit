"""TTS provider port の最小契約。

ここでは provider 固有の HTTP/CLI 実装を扱わない。``VoiceIntent`` は provider 非依存の
まま保持し、具体的なvoiceは adapter が解決して ``ProviderSynthesisRequest.voice`` へ
渡す。旧 ``yomiage.tts`` 実装との変換は ``adapters.tts`` に閉じ込める。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from ..core.models import VoiceIntent
from .artifacts import ArtifactRef

if TYPE_CHECKING:
    from ..application.voice_parameters import ResolvedVoiceParameters


class ProviderErrorCategory(str, Enum):
    """SynthesisService が扱う provider 失敗分類。"""

    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    SYNTHESIS = "synthesis"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


RETRYABLE_ERROR_CATEGORIES = frozenset(
    {
        ProviderErrorCategory.UNAVAILABLE,
        ProviderErrorCategory.TIMEOUT,
        ProviderErrorCategory.RATE_LIMIT,
    }
)


class ProviderOutputMode(str, Enum):
    """Providerが1 operationで生成できる出力形態。"""

    AUDIO_BYTES = "audio_bytes"
    LOCAL_FILE = "local_file"
    VM_FILE = "vm_file"
    REMOTE_PLAYBACK = "remote_playback"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """readinessとtarget選択に使うprovider capability。"""

    output_modes: frozenset[ProviderOutputMode] = frozenset(
        {ProviderOutputMode.AUDIO_BYTES}
    )
    audio_formats: frozenset[str] = frozenset({"wav"})
    voice_listing: bool = True
    cancellation: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "output_modes",
            frozenset(ProviderOutputMode(value) for value in self.output_modes),
        )
        formats = frozenset(str(value).lower().lstrip(".") for value in self.audio_formats)
        if not formats:
            raise ValueError("audio_formats must not be empty")
        object.__setattr__(self, "audio_formats", formats)

    def supports(self, target: SynthesisTarget) -> bool:
        return target.output_mode in self.output_modes and target.format in self.audio_formats


@dataclass(frozen=True, slots=True)
class MemoryTarget:
    """検証済みaudio bytesを返すtarget。"""

    format: str = "wav"
    output_mode: ProviderOutputMode = field(
        default=ProviderOutputMode.AUDIO_BYTES, init=False
    )


@dataclass(frozen=True, slots=True)
class ArtifactTarget:
    """canonical run rootからの相対pathへpublishするtarget。"""

    work_id: str
    run_id: str
    relative_path: str
    format: str = "wav"
    output_mode: ProviderOutputMode = field(
        default=ProviderOutputMode.LOCAL_FILE, init=False
    )

    def __post_init__(self) -> None:
        relative = PurePosixPath(self.relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError("relative_path must stay within the canonical run root")
        object.__setattr__(self, "relative_path", relative.as_posix())


@dataclass(frozen=True, slots=True)
class SharedFileTarget:
    """host artifactとVM共有rootで同じ相対位置を指すtarget。"""

    artifact: ArtifactTarget
    provider_relative_path: str | None = None
    format: str = "wav"
    output_mode: ProviderOutputMode = field(
        default=ProviderOutputMode.VM_FILE, init=False
    )

    def __post_init__(self) -> None:
        relative = PurePosixPath(
            self.provider_relative_path or self.artifact.relative_path
        )
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError("provider_relative_path must be a safe relative path")
        object.__setattr__(self, "provider_relative_path", relative.as_posix())
        object.__setattr__(self, "format", self.artifact.format)


@dataclass(frozen=True, slots=True)
class RemotePlaybackTarget:
    """Provider側deviceで再生し、audio bytesを要求しないtarget。"""

    format: str = "wav"
    output_mode: ProviderOutputMode = field(
        default=ProviderOutputMode.REMOTE_PLAYBACK, init=False
    )


SynthesisTarget = MemoryTarget | ArtifactTarget | SharedFileTarget | RemotePlaybackTarget


class ProviderError(RuntimeError):
    """provider実装から application 層へ渡す分類済みエラー。"""

    def __init__(
        self,
        message: str,
        *,
        category: ProviderErrorCategory | str = ProviderErrorCategory.UNKNOWN,
        provider: str | None = None,
        retryable: bool | None = None,
        details: Mapping[str, Any] | None = None,
        attempts: int = 0,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        try:
            resolved = (
                category
                if isinstance(category, ProviderErrorCategory)
                else ProviderErrorCategory(category)
            )
        except ValueError:
            resolved = ProviderErrorCategory.UNKNOWN
        self.message = str(message)
        self.category = resolved
        self.provider = provider
        self.retryable = (
            resolved in RETRYABLE_ERROR_CATEGORIES if retryable is None else bool(retryable)
        )
        self.details = dict(details or {})
        self.attempts = int(attempts)
        self.retry_count = int(retry_count)


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Provider の readiness 結果。"""

    healthy: bool
    provider: str = ""
    status: str = "unknown"
    message: str | None = None
    latency_ms: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True, slots=True)
class ProviderVoice:
    """Provider が公開する具体的なvoice。"""

    id: str
    name: str
    provider: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("voice id must not be empty")
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "provider", str(self.provider))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ProviderSynthesisRequest:
    """Provider へ渡す一回分の合成要求。"""

    text: str
    voice_intent: VoiceIntent
    voice: ProviderVoice | None = None
    utterance_id: str | None = None
    destination: str | None = None
    target: SynthesisTarget | None = None
    resolved_parameters: ResolvedVoiceParameters | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("text must not be empty")
        if not isinstance(self.voice_intent, VoiceIntent):
            raise TypeError("voice_intent must be VoiceIntent")
        if self.destination is None and isinstance(self.target, ArtifactTarget):
            object.__setattr__(self, "destination", self.target.relative_path)
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ProviderSynthesisResult:
    """bytes/file artifact を同じ結果で表す provider 出力。"""

    audio_data: bytes | None = None
    format: str = "wav"
    sample_rate: int | None = None
    duration: float | None = None
    artifact: ArtifactRef | None = None
    output_mode: ProviderOutputMode = ProviderOutputMode.AUDIO_BYTES
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.audio_data is not None and not isinstance(self.audio_data, bytes):
            object.__setattr__(self, "audio_data", bytes(self.audio_data))
        object.__setattr__(self, "format", str(self.format).lower().lstrip("."))
        object.__setattr__(self, "metadata", dict(self.metadata))


@runtime_checkable
class TTSProviderPort(Protocol):
    """SynthesisService が必要とする provider の最小 async 契約。"""

    name: str
    capabilities: ProviderCapabilities

    async def health(self) -> ProviderHealth: ...

    async def list_voices(self) -> list[ProviderVoice]: ...

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult: ...

    async def close(self) -> None: ...


def classify_provider_error(error: BaseException, *, provider: str | None = None) -> ProviderError:
    """旧実装や標準例外を provider 失敗分類へ変換する。"""

    if isinstance(error, ProviderError):
        if error.provider is None and provider is not None:
            return ProviderError(
                error.message,
                category=error.category,
                provider=provider,
                retryable=error.retryable,
                details=error.details,
                attempts=error.attempts,
                retry_count=error.retry_count,
            )
        return error
    if isinstance(error, asyncio.CancelledError):
        return ProviderError(
            "provider call cancelled",
            category=ProviderErrorCategory.CANCELLED,
            provider=provider,
            retryable=False,
        )
    name = type(error).__name__.lower()
    status = getattr(error, "status", getattr(error, "status_code", None))
    if status == 429 or "ratelimit" in name or "rate_limit" in name:
        category = ProviderErrorCategory.RATE_LIMIT
    elif status in {401, 403} or "auth" in name or "permission" in name:
        category = ProviderErrorCategory.AUTHENTICATION
    elif isinstance(error, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name:
        category = ProviderErrorCategory.TIMEOUT
    elif isinstance(error, (ConnectionError, OSError)) or any(
        token in name for token in ("unavailable", "connection", "network")
    ):
        category = ProviderErrorCategory.UNAVAILABLE
    elif isinstance(error, (ValueError, TypeError)) or "invalid" in name or "validation" in name:
        category = ProviderErrorCategory.INVALID_REQUEST
    else:
        category = ProviderErrorCategory.UNKNOWN
    return ProviderError(
        str(error) or name,
        category=category,
        provider=provider,
        retryable=category in RETRYABLE_ERROR_CATEGORIES,
        details={"status": status} if status is not None else None,
    )


def normalize_health(
    value: ProviderHealth | Mapping[str, Any] | bool, *, provider: str
) -> ProviderHealth:
    """adapterが返すhealth値をtyped contractへ正規化する。"""

    if isinstance(value, ProviderHealth):
        return (
            value
            if value.provider
            else ProviderHealth(
                value.healthy,
                provider=provider,
                status=value.status,
                message=value.message,
                latency_ms=value.latency_ms,
                details=value.details,
            )
        )
    if isinstance(value, Mapping):
        healthy = bool(value.get("healthy", value.get("available", value.get("ok", False))))
        return ProviderHealth(
            healthy,
            provider=str(value.get("provider", provider)),
            status=str(value.get("status", "healthy" if healthy else "unhealthy")),
            message=str(value["message"]) if value.get("message") is not None else None,
            latency_ms=value.get("latency_ms"),
            details={
                key: val
                for key, val in value.items()
                if key
                not in {"healthy", "available", "ok", "provider", "status", "message", "latency_ms"}
            },
        )
    return ProviderHealth(
        bool(value), provider=provider, status="healthy" if value else "unhealthy"
    )


def normalize_voice_list(values: Sequence[Any] | None, *, provider: str) -> list[ProviderVoice]:
    """旧dict/listのvoice返却をtyped値へ変換する。"""

    result: list[ProviderVoice] = []
    for value in values or []:
        if isinstance(value, ProviderVoice):
            result.append(value)
        elif isinstance(value, Mapping):
            raw = dict(value)
            voice_id = raw.pop("id", raw.pop("voice_id", None))
            if voice_id is None:
                continue
            name = raw.pop("name", raw.pop("label", str(voice_id)))
            result.append(ProviderVoice(str(voice_id), str(name), provider, raw))
        else:
            result.append(ProviderVoice(str(value), str(value), provider))
    return result


__all__ = [
    "ArtifactTarget",
    "MemoryTarget",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderErrorCategory",
    "ProviderHealth",
    "ProviderOutputMode",
    "ProviderSynthesisRequest",
    "ProviderSynthesisResult",
    "ProviderVoice",
    "RemotePlaybackTarget",
    "RETRYABLE_ERROR_CATEGORIES",
    "TTSProviderPort",
    "SharedFileTarget",
    "SynthesisTarget",
    "classify_provider_error",
    "normalize_health",
    "normalize_voice_list",
]
