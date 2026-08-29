"""Runtime findings F05/F08/F12 の音声公開境界回帰テスト。"""

from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path

import pytest

from yomiage.adapters.tts.legacy import LegacyTTSProviderAdapter
from yomiage.application.synthesis import SynthesisFailure, SynthesisRequest, SynthesisService
from yomiage.core.models import VoiceIntent
from yomiage.ports.tts import (
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
)
from yomiage.tts.audio_utils import concat_wav_bytes
from yomiage.tts.cache import TTSCache


def wav_bytes(
    frames: int = 100,
    *,
    sample_rate: int = 24_000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """小さい決定的なPCM WAV fixtureを返す。"""

    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(sample_width)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\0" * frames * channels * sample_width)
    return output.getvalue()


def wav_descriptor(data: bytes) -> tuple[int, int, int, int]:
    with wave.open(io.BytesIO(data), "rb") as stream:
        return (
            stream.getnframes(),
            stream.getframerate(),
            stream.getnchannels(),
            stream.getsampwidth(),
        )


def test_f05_concat_wav_bytes_rebuilds_header_from_frames() -> None:
    combined = concat_wav_bytes([wav_bytes(100), wav_bytes(100)])

    assert wav_descriptor(combined) == (200, 24_000, 1, 2)


@pytest.mark.parametrize(
    "second",
    [
        wav_bytes(sample_rate=48_000),
        wav_bytes(channels=2),
        wav_bytes(sample_width=1),
    ],
)
def test_f05_concat_wav_bytes_rejects_descriptor_mismatch(second: bytes) -> None:
    with pytest.raises(ValueError, match="format|descriptor|sample|channel|width|rate"):
        concat_wav_bytes([wav_bytes(), second])


class _BytesProvider:
    name = "fake"

    def __init__(self, audio: bytes, *, format: str = "wav") -> None:
        self.audio = audio
        self.format = format
        self.calls = 0

    async def health(self) -> ProviderHealth:
        return ProviderHealth(True, provider=self.name)

    async def list_voices(self) -> list:
        return []

    async def synthesize(self, _request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        self.calls += 1
        return ProviderSynthesisResult(audio_data=self.audio, format=self.format)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [b"<html>error</html>", b"RIFF\x00\x00", b"not-a-wav"])
async def test_f08_invalid_provider_audio_is_rejected_and_not_cached(
    tmp_path: Path, invalid: bytes
) -> None:
    provider = _BytesProvider(invalid)
    cache = TTSCache(tmp_path / "cache")
    service = SynthesisService(provider, cache=cache)

    with pytest.raises(SynthesisFailure):
        await service.synthesize("壊れた音声")

    assert list((tmp_path / "cache").glob("*")) == []
    await service.close()


@pytest.mark.asyncio
async def test_f08_declared_format_mismatch_is_rejected_before_cache_publish(
    tmp_path: Path,
) -> None:
    provider = _BytesProvider(wav_bytes(), format="mp3")
    cache = TTSCache(tmp_path / "cache")
    service = SynthesisService(provider, cache=cache)

    with pytest.raises(SynthesisFailure):
        await service.synthesize("format mismatch")

    assert provider.calls == 1
    assert list((tmp_path / "cache").glob("*")) == []
    await service.close()


@pytest.mark.asyncio
async def test_f08_corrupt_cache_entry_is_a_miss_and_is_replaced_atomically(
    tmp_path: Path,
) -> None:
    provider = _BytesProvider(wav_bytes())
    cache = TTSCache(tmp_path / "cache")
    request = SynthesisRequest("cache", voice_intent=VoiceIntent(pitch=0.25))
    service = SynthesisService(provider, cache=cache)
    # canonical schema keyで同じ論理requestの壊れたentryを先に置く。
    cache_key = service._cache_key("fake", request)
    corrupt_path = cache._path(f"v2-{cache_key}")
    corrupt_path.write_bytes(b"RIFF-truncated")

    result = await service.synthesize(request)

    assert provider.calls == 1
    assert result.cache_hit is False
    assert wav_descriptor(result.audio_data or b"")[0] == 100
    assert wav_descriptor(corrupt_path.read_bytes())[0] == 100
    await service.close()


def test_f08_cache_replace_interruption_keeps_previous_valid_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = TTSCache(tmp_path / "cache")
    key = "atomic-interruption"
    previous = wav_bytes(frames=40)
    replacement = wav_bytes(frames=80)
    cache.put_key(key, previous)
    final = cache._path(f"v2-{key}")

    def fail_replace(_self: Path, _target: Path) -> Path:
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        cache.put_key(key, replacement)

    assert final.read_bytes() == previous
    assert list(final.parent.glob(f".{final.name}.*.tmp")) == []


@pytest.mark.asyncio
async def test_f12_legacy_file_temp_is_removed_when_cancelled(tmp_path: Path) -> None:
    entered = asyncio.Event()

    class _BlockingFileProvider:
        name = "legacy"

        async def is_available(self) -> bool:
            return True

        async def synthesize_to_file(self, _text: str, output_path: str, **_kwargs: object):
            Path(output_path).write_bytes(wav_bytes())
            entered.set()
            await asyncio.Event().wait()

    adapter = LegacyTTSProviderAdapter(_BlockingFileProvider())
    final = tmp_path / "final.wav"
    task = asyncio.create_task(
        adapter.synthesize(
            ProviderSynthesisRequest(
                text="cancel",
                voice_intent=VoiceIntent(),
                destination=str(final),
            )
        )
    )
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert final.exists() is False
    assert list(tmp_path.glob(".final.wav.*")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["success", "exception", "timeout"])
async def test_f12_legacy_file_temp_cleanup_on_all_service_terminal_paths(
    tmp_path: Path, terminal: str
) -> None:
    class _FileProvider:
        name = "legacy"

        async def is_available(self) -> bool:
            return True

        async def synthesize_to_file(
            self, _text: str, output_path: str, **_kwargs: object
        ):
            Path(output_path).write_bytes(wav_bytes())
            if terminal == "exception":
                raise RuntimeError("provider failed")
            if terminal == "timeout":
                await asyncio.Event().wait()
            return type("Audio", (), {"audio_data": b"", "format": "wav"})()

    final = tmp_path / "final.wav"
    service = SynthesisService(
        LegacyTTSProviderAdapter(_FileProvider()),
        timeout=0.01 if terminal == "timeout" else None,
    )
    request = SynthesisRequest("本文", destination=str(final))

    if terminal == "success":
        result = await service.synthesize(request)
        assert result.audio_data
        assert final.is_file()
    else:
        with pytest.raises(SynthesisFailure):
            await service.synthesize(request)
        assert not final.exists()

    assert list(tmp_path.glob(".final.wav.*")) == []
    await service.close()
