"""既存TTSProvider実装を ``TTSProviderPort`` へ変換する。"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from ...application.voice_parameters import translate_provider_parameters
from ...ports.tts import (
    ArtifactTarget,
    ProviderCapabilities,
    ProviderHealth,
    ProviderOutputMode,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderVoice,
    RemotePlaybackTarget,
    TTSProviderPort,
    normalize_voice_list,
)
from ...tts.base import TTSProvider
from ...tts.shared_directory import VoisonaSharedDirectory


@runtime_checkable
class FileSynthesisProvider(Protocol):
    """明示的な legacy file-output 契約。

    旧Provider全体に ``synthesize_to_file`` が存在する前提で暗黙判定する代わりに、
    file-only実装はこの小さな契約を満たすものとしてadapterへ渡す。
    """

    async def synthesize_to_file(
        self, text: str, output_path: str, voice: str = "neutral", speed: float = 1.0, **params: Any
    ) -> Any: ...


class LegacyTTSProviderAdapter:
    """旧providerの ``synthesize(text, **kwargs)`` を新portへ適合する。"""

    def __init__(self, provider: Any, *, name: str | None = None) -> None:
        self.provider = provider
        self.name = name or str(provider.name)
        self.cache_version = getattr(provider, "cache_version", None) or getattr(
            provider, "version", None
        )
        if self.cache_version is None:
            self.cache_version = getattr(provider, "_unversioned_cache_id", None)
            if self.cache_version is None:
                self.cache_version = "unversioned-session:" + uuid4().hex
                try:
                    provider._unversioned_cache_id = self.cache_version
                except AttributeError:
                    # 属性を保持できない外部adapterはwrapper内だけで再利用する。
                    pass
        output_modes = {ProviderOutputMode.AUDIO_BYTES}
        if isinstance(provider, TTSProvider) or isinstance(provider, FileSynthesisProvider):
            output_modes.add(ProviderOutputMode.LOCAL_FILE)
        self.capabilities = ProviderCapabilities(output_modes=frozenset(output_modes))

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
        if request.resolved_parameters is not None and self.name in {
            "voisona",
            "voicevox",
            "voicepeak",
        }:
            for key in (
                "speed",
                "pitch",
                "volume",
                "intonation",
                "huskiness",
                "alp",
                "style_weights",
            ):
                kwargs.pop(key, None)
            kwargs["_native_parameters"] = self._native_parameters(request, voice)
        kwargs.setdefault("speed", request.voice_intent.speaking_rate)
        kwargs.setdefault("pitch", request.voice_intent.pitch)
        kwargs.setdefault("volume", request.voice_intent.volume)
        kwargs.setdefault("intonation", request.voice_intent.intonation)
        kwargs.setdefault("emotion", request.voice_intent.emotion.value)
        kwargs.setdefault("intensity", request.voice_intent.intensity)
        temporary_path: Path | None = None
        file_method = None
        if isinstance(self.provider, TTSProvider):
            file_method = self.provider.synthesize_to_file
        elif isinstance(self.provider, FileSynthesisProvider):
            file_method = self.provider.synthesize_to_file
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
        try:
            if inspect.isawaitable(value):
                value = await value
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
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
            output_mode=(
                ProviderOutputMode.LOCAL_FILE
                if isinstance(request.target, ArtifactTarget) and temporary_path is not None
                else ProviderOutputMode.AUDIO_BYTES
            ),
            metadata=metadata,
        )

    def _native_parameters(
        self,
        request: ProviderSynthesisRequest,
        voice: str,
    ) -> dict[str, Any]:
        """canonical値へlegacy provider固有の設定baselineを一度だけ合成する。"""

        resolved = request.resolved_parameters
        if resolved is None:
            return {}
        values = dict(translate_provider_parameters(self.name, resolved).values)
        if self.name == "voicevox":
            values["speedScale"] = float(getattr(self.provider, "speed_scale", 1.0)) * float(
                resolved.speaking_rate
            )
            values["pitchScale"] = float(getattr(self.provider, "pitch_scale", 0.0)) + float(
                resolved.pitch
            )
            values["intonationScale"] = float(
                getattr(self.provider, "intonation_scale", 1.0)
            ) * float(resolved.intonation)
        elif self.name == "voicepeak":
            pitch_scale = float(getattr(self.provider, "pitch_scale", 300.0))
            values["pitch"] = max(-300, min(300, round(float(resolved.pitch) * pitch_scale)))
        elif self.name == "voisona":
            baseline: dict[str, Any] = {
                "speed": 1.0,
                "pitch": 0.0,
                "volume": 0.0,
                "intonation": 1.0,
                "huskiness": 0.0,
                "alp": 0.0,
            }
            configured = getattr(self.provider, "_base_params", None)
            if isinstance(configured, dict):
                baseline.update(configured)
            tones = getattr(self.provider, "_tone_overrides", None)
            tone = tones.get(voice) if isinstance(tones, dict) else None
            if isinstance(tone, dict):
                baseline.update({key: value for key, value in tone.items() if value is not None})
            values = {
                "speed": max(
                    0.2,
                    min(5.0, float(baseline["speed"]) * float(resolved.speaking_rate)),
                ),
                "pitch": max(
                    -600,
                    min(600, float(baseline["pitch"]) + float(resolved.pitch)),
                ),
                "volume": max(
                    -8,
                    min(8, float(baseline["volume"]) + float(resolved.volume)),
                ),
                "intonation": max(
                    0,
                    min(
                        2,
                        float(baseline["intonation"]) * float(resolved.intonation),
                    ),
                ),
                "huskiness": max(
                    -20,
                    min(20, float(baseline["huskiness"]) + float(resolved.huskiness)),
                ),
                "alp": max(-1, min(1, float(baseline["alp"]) + float(resolved.alp))),
            }
            if resolved.style_weights:
                values["style_weights"] = resolved.style_weights
            elif "style_weights" in baseline:
                values["style_weights"] = baseline["style_weights"]
        return values

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
    """共有領域の一時 WAV を取得し、最終保存は ArtifactStore に任せる。

    最終出力先や run のディレクトリ構造を VM パスの推測に使わない。
    Memory / Batch / Studio は同じ共有領域を経由する。
    """

    def __init__(
        self,
        provider: Any,
        *,
        name: str | None = None,
        shared_directory: VoisonaSharedDirectory | None = None,
    ) -> None:
        super().__init__(provider, name=name)
        self.shared_directory = (
            shared_directory
            or getattr(provider, "shared_directory", None)
            or VoisonaSharedDirectory.from_config()
        )
        self.capabilities = ProviderCapabilities(
            output_modes=frozenset(
                {
                    ProviderOutputMode.AUDIO_BYTES,
                    ProviderOutputMode.LOCAL_FILE,
                    ProviderOutputMode.VM_FILE,
                    ProviderOutputMode.REMOTE_PLAYBACK,
                }
            )
        )

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        if isinstance(request.target, RemotePlaybackTarget):
            value = self.provider.synthesize(
                request.text,
                voice=(request.voice.id if request.voice else "neutral"),
                **self._provider_kwargs(request),
            )
            if inspect.isawaitable(value):
                value = await value
            return ProviderSynthesisResult(
                audio_data=None,
                format=getattr(value, "format", request.target.format),
                duration=getattr(value, "duration", None),
                output_mode=ProviderOutputMode.REMOTE_PLAYBACK,
                metadata={"remote_playback": True},
            )

        with self.shared_directory.temporary_wav() as (host_path, vm_path):
            value = self.provider.synthesize_to_file(
                request.text,
                vm_path,
                voice=str(request.voice.id if request.voice is not None else "neutral"),
                **self._provider_kwargs(request),
            )
            if inspect.isawaitable(value):
                value = await value
            data = getattr(value, "audio_data", b"") or host_path.read_bytes()
            return ProviderSynthesisResult(
                audio_data=data,
                format=getattr(value, "format", "wav"),
                sample_rate=getattr(value, "sample_rate", None),
                duration=getattr(value, "duration", None),
                output_mode=(
                    request.target.output_mode if request.target else ProviderOutputMode.AUDIO_BYTES
                ),
            )

    def _provider_kwargs(self, request: ProviderSynthesisRequest) -> dict[str, Any]:
        kwargs = dict(request.metadata)
        for key in (
            "run_dir",
            "vm_mount",
            "output_root",
            "entry_index",
            "sentence_index",
            "utterance_id",
        ):
            kwargs.pop(key, None)
        voice_override = kwargs.pop("voice_id", kwargs.pop("speaker_id", None))
        if voice_override is not None:
            kwargs["voice_id"] = voice_override
        if request.resolved_parameters is not None:
            for key in (
                "speed",
                "pitch",
                "volume",
                "intonation",
                "huskiness",
                "alp",
                "style_weights",
            ):
                kwargs.pop(key, None)
            voice = str(request.voice.id if request.voice is not None else "neutral")
            kwargs["_native_parameters"] = self._native_parameters(request, voice)
        kwargs.setdefault("speed", request.voice_intent.speaking_rate)
        kwargs.setdefault("pitch", request.voice_intent.pitch)
        kwargs.setdefault("volume", request.voice_intent.volume)
        kwargs.setdefault("intonation", request.voice_intent.intonation)
        kwargs.setdefault("emotion", request.voice_intent.emotion.value)
        kwargs.setdefault("intensity", request.voice_intent.intensity)
        return kwargs


__all__ = [
    "FileSynthesisProvider",
    "LegacyTTSProviderAdapter",
    "VoisonaFileProviderAdapter",
    "adapt_provider",
]
