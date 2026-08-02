"""Contract-test provider fakes.

The fakes intentionally model the three adapter shapes that the composition root accepts:
the new provider port, a legacy bytes provider, and a legacy file-only provider (VoiSona's
destination contract).  They are kept in tests so production adapters are exercised without
adding a test-only dependency to the application layer.
"""

from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path
from typing import Any

from yomiage.adapters.tts import LegacyTTSProviderAdapter, VoisonaFileProviderAdapter
from yomiage.ports.tts import (
    ProviderError,
    ProviderErrorCategory,
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderVoice,
)
from yomiage.tts.base import AudioResult


def wav_bytes(*, duration: float = 0.01, sample_rate: int = 16_000) -> bytes:
    """Return a small valid mono PCM WAV payload for artifact validation tests."""

    frames = int(sample_rate * duration)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frames)
    return stream.getvalue()


class PortFakeProvider:
    """Deterministic implementation of ``TTSProviderPort`` for contract tests."""

    def __init__(
        self,
        name: str = "fake",
        *,
        delay: float = 0.0,
        fail: bool = False,
        failures: int = 0,
        audio_data: bytes | None = None,
    ) -> None:
        self.name = name
        self.delay = delay
        self.fail = fail
        self.failures = failures
        self.audio_data = audio_data or wav_bytes()
        self.calls: list[ProviderSynthesisRequest] = []
        self.cancelled = 0
        self.close_calls = 0

    async def health(self) -> ProviderHealth:
        return ProviderHealth(True, provider=self.name, status="healthy")

    async def list_voices(self) -> list[ProviderVoice]:
        return [
            ProviderVoice(
                id=f"{self.name}-voice",
                name=f"{self.name} voice",
                provider=self.name,
                metadata={"profile": self.name},
            )
        ]

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        self.calls.append(request)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.fail or self.failures:
            if self.failures:
                self.failures -= 1
            raise ProviderError(
                f"{self.name} failed",
                category=ProviderErrorCategory.SYNTHESIS,
                provider=self.name,
                retryable=False,
            )
        return ProviderSynthesisResult(
            audio_data=self.audio_data,
            format="wav",
            sample_rate=16_000,
            duration=0.01,
        )

    async def close(self) -> None:
        self.close_calls += 1


class LegacyFakeProvider:
    """Legacy bytes provider consumed through ``LegacyTTSProviderAdapter``."""

    def __init__(
        self,
        name: str = "legacy",
        *,
        delay: float = 0.0,
        fail: bool = False,
        audio_data: bytes | None = None,
    ) -> None:
        self.name = name
        self.delay = delay
        self.fail = fail
        self.audio_data = audio_data or wav_bytes()
        self.calls: list[dict[str, Any]] = []
        self.cancelled = 0
        self.close_calls = 0

    async def is_available(self) -> bool:
        return True

    async def list_voices(self) -> list[dict[str, str]]:
        return [{"id": f"{self.name}-voice", "name": f"{self.name} voice"}]

    async def synthesize(self, text: str, **kwargs: Any) -> AudioResult:
        self.calls.append({"text": text, **kwargs})
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.fail:
            raise ProviderError(
                f"{self.name} failed",
                category=ProviderErrorCategory.SYNTHESIS,
                provider=self.name,
                retryable=False,
            )
        return AudioResult(
            audio_data=self.audio_data,
            format="wav",
            sample_rate=16_000,
            duration=0.01,
        )

    async def close(self) -> None:
        self.close_calls += 1


class FileLegacyFakeProvider(LegacyFakeProvider):
    """Legacy file-only provider with a local stand-in for a VM shared mount."""

    def __init__(self, root: Path, **kwargs: Any) -> None:
        super().__init__(name="voisona", **kwargs)
        self.root = Path(root)
        self.file_calls: list[dict[str, Any]] = []

    async def synthesize(self, text: str, **kwargs: Any) -> AudioResult:
        # VoiSona's adapter should use synthesize_to_file whenever available.
        return await super().synthesize(text, **kwargs)

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        **kwargs: Any,
    ) -> AudioResult:
        self.file_calls.append({"text": text, "output_path": str(output_path), **kwargs})
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.fail:
            raise ProviderError(
                f"{self.name} failed",
                category=ProviderErrorCategory.SYNTHESIS,
                provider=self.name,
                retryable=False,
            )

        path_value = str(output_path)
        # VM paths are ``Z:\\run-name\\filename``.  The local test root stands in for the
        # mounted run directory and only needs the basename to emulate the shared file.  A
        # normal local destination is written in place, matching the legacy adapter contract.
        if "\\" in path_value:
            local_path = self.root / Path(path_value.rsplit("\\", 1)[-1]).name
        else:
            local_path = Path(output_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.audio_data)
        return AudioResult(audio_data=b"", format="wav", sample_rate=16_000, duration=0.01)


def provider_factory(kind: str, tmp_path: Path, **kwargs: Any):
    """Construct an adapter/provider pair and return ``(adapter, underlying_fake)``."""

    if kind == "port":
        fake = PortFakeProvider(name=str(kwargs.pop("name", "port")), **kwargs)
        return fake, fake
    if kind == "legacy":
        fake = LegacyFakeProvider(name=str(kwargs.pop("name", "legacy")), **kwargs)
        return LegacyTTSProviderAdapter(fake), fake
    if kind == "file":
        kwargs.pop("name", None)
        fake = FileLegacyFakeProvider(tmp_path / "vm", **kwargs)
        return VoisonaFileProviderAdapter(fake), fake
    raise ValueError(f"unknown contract provider kind: {kind}")


PROVIDER_KINDS = ("port", "legacy", "file")
