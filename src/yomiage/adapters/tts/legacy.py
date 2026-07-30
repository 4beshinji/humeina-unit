"""既存TTSProvider実装を ``TTSProviderPort`` へ変換する。"""

from __future__ import annotations

import inspect
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ...ports.tts import (
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderVoice,
    TTSProviderPort,
    normalize_voice_list,
)
from ...tts.base import TTSProvider


class LegacyTTSProviderAdapter:
    """旧providerの ``synthesize(text, **kwargs)`` を新portへ適合する。"""

    def __init__(self, provider: Any, *, name: str | None = None) -> None:
        self.provider = provider
        self.name = name or str(provider.name)

    async def health(self) -> ProviderHealth:
        method = getattr(self.provider, "is_available", None)
        if callable(method):
            value = method()
            if inspect.isawaitable(value):
                value = await value
            return ProviderHealth(
                bool(value), provider=self.name, status="healthy" if value else "unhealthy"
            )
        healthy = bool(getattr(self.provider, "healthy", True))
        return ProviderHealth(
            healthy, provider=self.name, status="healthy" if healthy else "unhealthy"
        )

    async def list_voices(self) -> list[ProviderVoice]:
        method = getattr(self.provider, "list_voices", None)
        if not callable(method):
            return []
        value = method()
        if inspect.isawaitable(value):
            value = await value
        return normalize_voice_list(value, provider=self.name)

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        kwargs = dict(request.metadata)
        # Batch adapters pass a few execution-only values (run directory, VM mount, and
        # provider-specific voice override) through request metadata.  They must never leak
        # into the legacy provider's public kwargs.
        voice_override = kwargs.pop("voice_id", None)
        if voice_override is None:
            voice_override = kwargs.pop("speaker_id", None)
        if voice_override is not None:
            # Legacy providers disagree on whether the concrete voice is a ``voice`` argument
            # or a provider-specific ``voice_id`` kwarg.  Keep both representations at this
            # adapter boundary; neither leaks into the domain VoiceIntent.
            kwargs["voice_id"] = voice_override
        for key in ("run_dir", "vm_mount", "entry_index", "sentence_index", "utterance_id"):
            kwargs.pop(key, None)
        voice = str(
            voice_override or (request.voice.id if request.voice is not None else "neutral")
        )
        kwargs.setdefault("speed", request.voice_intent.speaking_rate)
        kwargs.setdefault("pitch", request.voice_intent.pitch)
        kwargs.setdefault("volume", request.voice_intent.volume)
        kwargs.setdefault("intonation", request.voice_intent.intonation)
        kwargs.setdefault("emotion", request.voice_intent.emotion.value)
        kwargs.setdefault("intensity", request.voice_intent.intensity)
        temporary_path: Path | None = None
        file_method = getattr(self.provider, "synthesize_to_file", None)
        if request.destination and callable(file_method):
            target = Path(request.destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                suffix=target.suffix or ".wav",
                prefix=f".{target.name}.",
                dir=target.parent,
                delete=False,
            )
            handle.close()
            temporary_path = Path(handle.name)
            value = file_method(request.text, temporary_path, voice=voice, **kwargs)
        else:
            value = self.provider.synthesize(request.text, voice=voice, **kwargs)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, ProviderSynthesisResult):
            return value
        data = getattr(value, "audio_data", value if isinstance(value, bytes) else None)
        # Some legacy providers expose file synthesis as the only way to obtain audio bytes
        # (notably VoiSona).  Preserve the old SDK behavior even when the caller did not ask
        # for a destination by using a short-lived local file.
        if not data and temporary_path is None and callable(file_method):
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            handle.close()
            temporary_path = Path(handle.name)
            try:
                value = file_method(request.text, temporary_path, voice=voice, **kwargs)
                if inspect.isawaitable(value):
                    value = await value
                data = getattr(value, "audio_data", value if isinstance(value, bytes) else None)
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise
        metadata: dict[str, Any] = {}
        if temporary_path is not None:
            if not data and temporary_path.is_file():
                data = temporary_path.read_bytes()
            metadata["file_path"] = str(temporary_path)
            metadata["_cleanup_file"] = True
        return ProviderSynthesisResult(
            audio_data=data,
            format=getattr(value, "format", "wav"),
            sample_rate=getattr(value, "sample_rate", None),
            duration=getattr(value, "duration", None),
            metadata=metadata,
        )

    async def close(self) -> None:
        method = getattr(self.provider, "close", None)
        if callable(method):
            # A few lightweight legacy fakes intentionally omit TTSProvider.__init__.  Their
            # inherited close() assumes ``_session`` exists; there is nothing to release in
            # that case, so keep lifecycle cleanup idempotent for the compatibility adapter.
            if isinstance(self.provider, TTSProvider) and not hasattr(self.provider, "_session"):
                return
            value = method()
            if inspect.isawaitable(value):
                await value


def adapt_provider(provider: Any, *, name: str | None = None) -> TTSProviderPort:
    """旧providerを新portとして利用するcomposition-root向けhelper。"""

    if isinstance(provider, LegacyTTSProviderAdapter):
        return provider
    return LegacyTTSProviderAdapter(provider, name=name)


class VoisonaFileProviderAdapter(LegacyTTSProviderAdapter):
    """VoiSona ``destination=file`` を local artifact へ適合するadapter。

    VoiSona Talk は Windows/VM 側のパスを要求するため、Batch の run directoryをそのまま
    APIへ渡すことはできない。run directoryと同名の一時ファイルを VM mount 下へ指定し、
    共有マウント上へ現れた WAV を ``ProviderSynthesisResult`` の bytes として返す。
    以降の検証・atomic publishは SynthesisService/ArtifactStore が担当する。
    """

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        if not request.destination:
            return await super().synthesize(request)

        metadata = dict(request.metadata)
        run_dir_value = metadata.get("run_dir")
        vm_mount = str(metadata.get("vm_mount") or "Z:")
        if not run_dir_value:
            # SDK/非Batch callersには通常のLegacy adapter semanticsを残す。
            return await super().synthesize(request)

        run_dir = Path(str(run_dir_value)).expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(request.destination).name
        temporary = run_dir / f".{filename}.{uuid.uuid4().hex}.tmp.wav"
        vm_path = f"{vm_mount}\\{run_dir.name}\\{temporary.name}"

        kwargs = dict(metadata)
        kwargs.pop("run_dir", None)
        kwargs.pop("vm_mount", None)
        voice_override = kwargs.pop("voice_id", None)
        if voice_override is None:
            voice_override = kwargs.pop("speaker_id", None)
        if voice_override is not None:
            kwargs["voice_id"] = voice_override
        for key in ("entry_index", "sentence_index", "utterance_id"):
            kwargs.pop(key, None)
        voice = str(
            voice_override or (request.voice.id if request.voice is not None else "neutral")
        )
        kwargs.setdefault("speed", request.voice_intent.speaking_rate)
        kwargs.setdefault("pitch", request.voice_intent.pitch)
        kwargs.setdefault("volume", request.voice_intent.volume)
        kwargs.setdefault("intonation", request.voice_intent.intonation)
        kwargs.setdefault("emotion", request.voice_intent.emotion.value)
        kwargs.setdefault("intensity", request.voice_intent.intensity)

        try:
            value = self.provider.synthesize_to_file(
                request.text,
                vm_path,
                voice=voice,
                **kwargs,
            )
            if inspect.isawaitable(value):
                value = await value
            data = getattr(value, "audio_data", b"") or b""
            # On a shared VM mount the local side sees this path after VoiSona reports
            # ``succeeded``.  Reading it here makes the result indistinguishable from a
            # bytes-producing provider to SynthesisService.
            if not data and temporary.is_file():
                data = temporary.read_bytes()
            return ProviderSynthesisResult(
                audio_data=data or None,
                format=getattr(value, "format", "wav"),
                sample_rate=getattr(value, "sample_rate", None),
                duration=getattr(value, "duration", None),
                metadata={"file_path": str(temporary), "_cleanup_file": True},
            )
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


__all__ = ["LegacyTTSProviderAdapter", "VoisonaFileProviderAdapter", "adapt_provider"]
