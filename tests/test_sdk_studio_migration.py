"""SYN-002 compatibility facade and Studio service wiring contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from yomiage.api.bridge import TTSBridge
from yomiage.application.synthesis import SynthesisService
from yomiage.ports.tts import (
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderVoice,
)
from yomiage.studio.engine import StudioEngine
from yomiage.tts.base import AudioResult, TTSProvider


class PortProvider:
    def __init__(self, name: str = "fake", *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[ProviderSynthesisRequest] = []
        self.closed = False

    async def health(self) -> ProviderHealth:
        return ProviderHealth(True, provider=self.name, status="healthy")

    async def list_voices(self) -> list[ProviderVoice]:
        return [ProviderVoice(f"{self.name}-voice", self.name, self.name)]

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("provider down")
        return ProviderSynthesisResult(audio_data=b"RIFF-fake", duration=0.1)

    async def close(self) -> None:
        self.closed = True


def test_bridge_external_service_is_not_closed_by_facade() -> None:
    async def run() -> None:
        provider = PortProvider()
        service = SynthesisService(provider)
        bridge = TTSBridge.from_service(service)

        result = await bridge.synthesize("テスト", voice_id="fake-voice")
        assert result.audio_data == b"RIFF-fake"
        assert isinstance(bridge.service, SynthesisService)

        await bridge.close()
        assert provider.closed is False
        await service.close()
        assert provider.closed is True

    asyncio.run(run())


def test_bridge_fallback_resolves_provider_voice_again() -> None:
    class LegacyProvider(TTSProvider):
        def __init__(self, name: str, fail: bool = False) -> None:
            super().__init__()
            self._name = name
            self.fail = fail
            self.calls: list[dict] = []

        @property
        def name(self) -> str:
            return self._name

        async def is_available(self) -> bool:
            return True

        async def list_voices(self) -> list[dict]:
            return [{"id": f"{self._name}-voice", "label": self._name}]

        async def synthesize(
            self, text: str, voice: str = "neutral", **kwargs: object
        ) -> AudioResult:
            self.calls.append({"voice": voice, **kwargs})
            if self.fail:
                raise RuntimeError("provider down")
            return AudioResult(audio_data=b"fallback")

    async def run() -> None:
        primary = LegacyProvider("primary", fail=True)
        fallback = LegacyProvider("fallback")
        bridge = TTSBridge(primary, fallback)

        result = await bridge.synthesize("テスト", voice_id="primary-voice")
        assert result.audio_data == b"fallback"
        assert fallback.calls[0]["voice"] == "fallback-voice"
        assert "voice_id" not in fallback.calls[0]
        await bridge.close()

    asyncio.run(run())


def test_studio_uses_injected_service_without_taking_ownership(tmp_path: Path) -> None:
    async def run() -> None:
        provider = PortProvider()
        service = SynthesisService(provider)
        script = tmp_path / "script.txt"
        script.write_text("霊夢: こんにちは\n", encoding="utf-8")
        engine = StudioEngine(
            {
                "studio": {
                    "default_provider": "fake",
                    "default_format": "plain",
                    "cache_enabled": False,
                }
            },
            output_dir=tmp_path / "output",
            synthesis_service=service,
        )
        def fail_if_provider_construction_is_attempted(mappings: dict) -> dict:
            raise AssertionError("injected synthesis service must skip provider construction")

        engine._create_providers = fail_if_provider_construction_is_attempted

        project = await engine.synth(script, provider="fake", output_format="plain")
        assert project.results[0].wav_path.read_bytes() == b"RIFF-fake"
        assert len(provider.calls) == 1
        assert provider.closed is False
        await service.close()

    asyncio.run(run())
