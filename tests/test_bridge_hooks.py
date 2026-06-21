"""Tests for TTSBridge hooks and metrics integration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from yomiage.api.bridge import TTSBridge
from yomiage.api.hooks import Event, EventHooks, EventType
from yomiage.api.metrics import MetricsCollector
from yomiage.tts.base import AudioResult, TTSProvider
from yomiage.tts.cache import TTSCache


class MockTTSProvider(TTSProvider):
    """テスト用モックTTSプロバイダー."""

    @property
    def name(self) -> str:
        return "mock"

    async def synthesize(self, text, voice="neutral", speed=1.0, **params):
        return AudioResult(
            audio_data=b"RIFF_mock", format="wav", sample_rate=24000, duration=1.0
        )

    async def is_available(self) -> bool:
        return True


class TestTTSBridgeHooks:
    @pytest.mark.asyncio
    async def test_synthesis_events(self):
        hooks = EventHooks()
        metrics = MetricsCollector()
        events: list[Event] = []

        @hooks.on(EventType.SYNTHESIS_START)
        @hooks.on(EventType.SYNTHESIS_END)
        def handler(event: Event) -> None:
            events.append(event)

        bridge = TTSBridge.from_provider(
            MockTTSProvider(), hooks=hooks, metrics=metrics
        )
        result = await bridge.synthesize("こんにちは")

        assert result.audio_data == b"RIFF_mock"
        start_events = [e for e in events if e.type == EventType.SYNTHESIS_START]
        end_events = [e for e in events if e.type == EventType.SYNTHESIS_END]
        assert len(start_events) == 1
        assert len(end_events) == 1
        assert end_events[0].payload["text"] == "こんにちは"
        assert end_events[0].payload["cache_hit"] is False

        assert metrics.synthesis_total == 1
        assert metrics.synthesis_cache_misses == 1

    @pytest.mark.asyncio
    async def test_cache_hit_event(self):
        hooks = EventHooks()
        metrics = MetricsCollector()
        events: list[Event] = []

        @hooks.on(EventType.SYNTHESIS_CACHE_HIT)
        def handler(event: Event) -> None:
            events.append(event)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TTSCache(cache_dir=Path(tmpdir))
            bridge = TTSBridge.from_provider(
                MockTTSProvider(), hooks=hooks, metrics=metrics, cache=cache
            )
            result1 = await bridge.synthesize("こんにちは")
            result2 = await bridge.synthesize("こんにちは")

        assert result1.audio_data == result2.audio_data
        cache_hit_events = [
            e for e in events if e.type == EventType.SYNTHESIS_CACHE_HIT
        ]
        assert len(cache_hit_events) == 1
        assert metrics.synthesis_cache_hits == 1
        assert metrics.synthesis_cache_misses == 1

    @pytest.mark.asyncio
    async def test_synthesis_error_event(self):
        hooks = EventHooks()
        metrics = MetricsCollector()
        events: list[Event] = []

        @hooks.on(EventType.SYNTHESIS_ERROR)
        def handler(event: Event) -> None:
            events.append(event)

        class FailingProvider(TTSProvider):
            @property
            def name(self) -> str:
                return "failing"

            async def synthesize(self, text, **params):
                raise RuntimeError("always fails")

            async def is_available(self) -> bool:
                return True

        bridge = TTSBridge.from_provider(FailingProvider(), hooks=hooks, metrics=metrics)
        with pytest.raises(RuntimeError):
            await bridge.synthesize("こんにちは")

        assert len(events) == 1
        assert events[0].payload["error"] == "always fails"
        assert metrics.synthesis_errors == 1
