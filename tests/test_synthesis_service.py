"""SYN-001 SynthesisService/provider contract tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from yomiage.adapters.persistence.filesystem import FilesystemArtifactStore
from yomiage.adapters.tts import LegacyTTSProviderAdapter
from yomiage.application.synthesis import (
    RetryPolicy,
    SynthesisFailure,
    SynthesisRequest,
    SynthesisService,
)
from yomiage.application.voice_parameters import VoiceResolutionContext
from yomiage.core.models import VoiceIntent
from yomiage.ports.tts import (
    ProviderError,
    ProviderErrorCategory,
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderVoice,
)
from yomiage.tts.audio_utils import write_silence_wav

from .test_audio_validation import wav_bytes


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        failures: int = 0,
        delay: float = 0.0,
        audio_data: bytes | None = None,
    ) -> None:
        self.name = name
        self.failures = failures
        self.delay = delay
        self.audio_data = wav_bytes() if audio_data is None else audio_data
        self.calls: list[ProviderSynthesisRequest] = []
        self.closed = False

    async def health(self) -> ProviderHealth:
        return ProviderHealth(True, provider=self.name, status="healthy")

    async def list_voices(self) -> list[ProviderVoice]:
        return [
            ProviderVoice(
                f"{self.name}-voice",
                f"{self.name} voice",
                self.name,
                {"profile": self.name},
            )
        ]

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        self.calls.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failures:
            self.failures -= 1
            raise ProviderError(
                f"{self.name} transient failure",
                category=ProviderErrorCategory.UNAVAILABLE,
                provider=self.name,
            )
        return ProviderSynthesisResult(audio_data=self.audio_data)

    async def close(self) -> None:
        self.closed = True


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def put(self, key: str, value: bytes) -> None:
        self.values[key] = value


@pytest.mark.asyncio
async def test_normal_synthesis_returns_typed_result_and_metadata() -> None:
    provider = FakeProvider("primary")
    service = SynthesisService(provider)

    result = await service.synthesize(
        SynthesisRequest("こんにちは", voice_intent=VoiceIntent(preferred_profile="primary"))
    )

    assert result.audio_data == provider.audio_data
    assert result.provider == "primary"
    assert result.status == "synthesized"
    assert result.metadata["fallback_used"] is False
    assert provider.calls[0].voice is not None
    assert provider.calls[0].voice.id == "primary-voice"
    await service.close()


@pytest.mark.asyncio
async def test_retry_then_fallback_resolves_voice_again() -> None:
    primary = FakeProvider("primary", failures=2)
    fallback = FakeProvider("fallback")
    service = SynthesisService(
        {"primary": primary, "fallback": fallback},
        primary="primary",
        fallback="fallback",
        retry_policy=RetryPolicy(max_retries=1),
    )

    result = await service.synthesize("本文")

    assert result.status == "fallback_synthesized"
    assert result.fallback_used
    assert result.metadata["retry_count"] == 1
    assert len(primary.calls) == 2
    assert fallback.calls[0].voice is not None
    assert fallback.calls[0].voice.id == "fallback-voice"
    await service.close()


@pytest.mark.asyncio
async def test_cache_hit_is_recorded_and_provider_is_not_called_twice() -> None:
    provider = FakeProvider("primary")
    cache = MemoryCache()
    service = SynthesisService(provider, cache=cache)

    first = await service.synthesize("cache me")
    second = await service.synthesize("cache me")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.metadata["cache_hit"] is True
    assert len(provider.calls) == 1
    await service.close()


@pytest.mark.asyncio
async def test_timeout_is_classified_and_not_retried_when_policy_disallows() -> None:
    provider = FakeProvider("primary", delay=0.05)
    service = SynthesisService(provider, timeout=0.001)

    with pytest.raises(SynthesisFailure) as caught:
        await service.synthesize("slow")

    assert caught.value.category is ProviderErrorCategory.TIMEOUT
    assert len(provider.calls) == 1
    await service.close()


@pytest.mark.asyncio
async def test_f10_single_deadline_includes_voice_resolution_retry_and_fallback() -> None:
    class SlowVoiceProvider(FakeProvider):
        async def list_voices(self) -> list[ProviderVoice]:
            await asyncio.sleep(0.03)
            return await super().list_voices()

    primary = SlowVoiceProvider("primary", failures=10)
    fallback = FakeProvider("fallback")
    service = SynthesisService(
        {"primary": primary, "fallback": fallback},
        primary="primary",
        fallback="fallback",
        timeout=0.04,
        retry_policy=RetryPolicy(max_retries=2, backoff_factor=0.03),
    )
    started = time.monotonic()

    with pytest.raises(SynthesisFailure) as caught:
        await service.synthesize("operation deadline")

    assert caught.value.category is ProviderErrorCategory.TIMEOUT
    assert time.monotonic() - started < 0.08
    assert len(primary.calls) == 1
    assert fallback.calls == []
    await service.close()


@pytest.mark.asyncio
async def test_cancellation_is_propagated_without_fallback_or_retry() -> None:
    provider = FakeProvider("primary", delay=0.1)
    fallback = FakeProvider("fallback")
    service = SynthesisService(
        {"primary": provider, "fallback": fallback}, primary="primary", fallback="fallback"
    )
    task = asyncio.create_task(service.synthesize("cancel"))
    await asyncio.sleep(0.005)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(provider.calls) == 1
    assert fallback.calls == []
    await service.close()


@pytest.mark.asyncio
async def test_provider_concurrency_is_bounded() -> None:
    class TrackingProvider(FakeProvider):
        active = 0
        maximum = 0

        async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                await asyncio.sleep(0.01)
                return ProviderSynthesisResult(audio_data=wav_bytes())
            finally:
                self.active -= 1

    provider = TrackingProvider("primary")
    service = SynthesisService(provider, concurrency=1)
    await asyncio.gather(*(service.synthesize(f"text-{index}") for index in range(3)))
    assert provider.maximum == 1
    assert provider.active == 0
    await service.close()


@pytest.mark.asyncio
async def test_bytes_are_published_as_verified_file_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    write_silence_wav(source, 0.01)
    store = FilesystemArtifactStore(tmp_path / "output")
    provider = FakeProvider("primary", audio_data=source.read_bytes())
    service = SynthesisService(provider, artifact_store=store)

    result = await service.synthesize(
        SynthesisRequest(
            "file",
            work_id="work",
            run_id="run",
            utterance_id="u-1",
            destination="audio/u-1.wav",
        )
    )

    assert result.artifact is not None
    assert result.artifact.verified
    assert result.artifact.path.is_file()
    assert store.verify_manifest("work", "run") == ()
    await service.close()


@pytest.mark.asyncio
async def test_legacy_file_provider_is_validated_before_final_publish(tmp_path: Path) -> None:
    class LegacyFileProvider:
        name = "legacy"

        def __init__(self) -> None:
            self.destination: Path | None = None

        async def is_available(self) -> bool:
            return True

        async def list_voices(self) -> list[dict[str, str]]:
            return [{"id": "legacy-voice", "name": "legacy"}]

        async def synthesize_to_file(
            self, text: str, output_path: str | Path, **_: object
        ) -> object:
            self.destination = Path(output_path)
            write_silence_wav(self.destination, 0.01)
            return type("Audio", (), {"audio_data": b"", "format": "wav"})()

        async def close(self) -> None:
            return None

    legacy = LegacyFileProvider()
    store = FilesystemArtifactStore(tmp_path / "output")
    service = SynthesisService(
        LegacyTTSProviderAdapter(legacy),
        artifact_store=store,
    )

    result = await service.synthesize(
        SynthesisRequest(
            "legacy file",
            work_id="work",
            run_id="run",
            utterance_id="u-1",
            destination="audio/u-1.wav",
        )
    )

    assert result.artifact is not None and result.artifact.verified
    assert "_cleanup_file" not in result.metadata
    assert "file_path" not in result.metadata
    assert legacy.destination is not None
    assert legacy.destination != store.run_dir("work", "run") / "audio" / "u-1.wav"
    assert not legacy.destination.exists()
    await service.close()


@pytest.mark.asyncio
async def test_close_is_explicit_and_idempotent() -> None:
    primary = FakeProvider("primary")
    fallback = FakeProvider("fallback")
    service = SynthesisService(
        {"primary": primary, "fallback": fallback}, primary="primary", fallback="fallback"
    )

    await service.close()
    await service.close()

    assert primary.closed and fallback.closed


@pytest.mark.asyncio
async def test_f17_file_cache_key_includes_voice_intent(tmp_path: Path) -> None:
    from yomiage.tts.cache import TTSCache

    wav_path = tmp_path / "fixture.wav"
    write_silence_wav(wav_path, 0.01)
    provider = FakeProvider("primary", audio_data=wav_path.read_bytes())
    service = SynthesisService(provider, cache=TTSCache(tmp_path / "cache"))

    await service.synthesize(
        SynthesisRequest("same", voice_intent=VoiceIntent(pitch=-0.25, emotion="sad"))
    )
    await service.synthesize(
        SynthesisRequest("same", voice_intent=VoiceIntent(pitch=0.25, emotion="happy"))
    )

    assert len(provider.calls) == 2
    assert len(list((tmp_path / "cache").glob("*.wav"))) == 2
    await service.close()


@pytest.mark.asyncio
async def test_f17_file_cache_key_includes_resolved_scene_parameters(
    tmp_path: Path,
) -> None:
    from yomiage.tts.cache import TTSCache

    provider = FakeProvider("primary")
    service = SynthesisService(provider, cache=TTSCache(tmp_path / "cache"))
    intent = VoiceIntent(emotion="neutral", intensity=0.0)

    await service.synthesize(
        SynthesisRequest(
            "same",
            voice_intent=intent,
            parameter_context=VoiceResolutionContext(scene={"pitch": -0.25}),
        )
    )
    await service.synthesize(
        SynthesisRequest(
            "same",
            voice_intent=intent,
            parameter_context=VoiceResolutionContext(scene={"pitch": 0.25}),
        )
    )

    assert len(provider.calls) == 2
    resolved = [request.resolved_parameters for request in provider.calls]
    assert all(value is not None for value in resolved)
    assert [value.pitch for value in resolved if value is not None] == [-0.25, 0.25]
    assert len(list((tmp_path / "cache").glob("*.wav"))) == 2
    await service.close()


@pytest.mark.asyncio
async def test_d03_concurrent_identical_cache_miss_is_single_flight(tmp_path: Path) -> None:
    wav_path = tmp_path / "fixture.wav"
    write_silence_wav(wav_path, 0.01)

    class BarrierProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__("primary", audio_data=wav_path.read_bytes())
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def synthesize(
            self, request: ProviderSynthesisRequest
        ) -> ProviderSynthesisResult:
            self.calls.append(request)
            self.entered.set()
            await self.release.wait()
            return ProviderSynthesisResult(audio_data=self.audio_data)

    provider = BarrierProvider()
    cache = MemoryCache()
    service = SynthesisService(provider, cache=cache, concurrency=2)
    first = asyncio.create_task(service.synthesize("same"))
    second = asyncio.create_task(service.synthesize("same"))
    await provider.entered.wait()
    await asyncio.sleep(0)
    provider.release.set()
    results = await asyncio.gather(first, second)

    assert len(provider.calls) == 1
    assert results[0].audio_data == results[1].audio_data
    assert len(cache.values) == 1
    assert service._flight_locks == {}
    await service.close()
