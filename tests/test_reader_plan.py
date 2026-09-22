"""SYN-003 ReadingEngine/UtterancePlaybackQueue contract tests."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from yomiage.application.synthesis import SynthesisService
from yomiage.core.models import SegmentType, SourceRef, Utterance, VoiceIntent
from yomiage.core.plan import UtterancePlan
from yomiage.ports.tts import (
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
)
from yomiage.reader.bookmark import BookmarkManager
from yomiage.reader.engine import ReadingEngine
from yomiage.reader.param_mapper import ParamMapper
from yomiage.reader.playback import UtterancePlaybackQueue
from yomiage.tts.base import AudioResult

from .test_audio_validation import wav_bytes


def _audio_for(text: str) -> bytes:
    return wav_bytes(frames=max(1, len(text)))


class FakeProvider:
    name = "fake"

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[ProviderSynthesisRequest] = []
        self.cancelled = 0
        self.closed = False

    async def health(self) -> ProviderHealth:
        return ProviderHealth(True, provider=self.name, status="healthy")

    async def list_voices(self):
        return []

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        self.calls.append(request)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return ProviderSynthesisResult(audio_data=_audio_for(request.text))

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeTTSManager:
    primary: FakeProvider
    fallback: None = None

    def _is_slow_provider(self) -> bool:
        return False

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class LegacyProvider:
    name = "legacy"
    healthy = True
    is_slow = False

    def __init__(self) -> None:
        self.closed = False

    async def is_available(self) -> bool:
        return True

    async def synthesize(self, text: str, **_kwargs) -> AudioResult:
        return AudioResult(audio_data=_audio_for(text))

    async def close(self) -> None:
        self.closed = True


def make_plan() -> UtterancePlan:
    return UtterancePlan(
        schema_version=1,
        plan_id="plan-reader",
        source_fingerprint="source-reader",
        config_fingerprint="config-reader",
        utterances=(
            Utterance(
                id="u-0000",
                order=0,
                text="alpha",
                segment_type=SegmentType.DIALOGUE,
                speaker_id="alice",
                source_ref=SourceRef(url="https://example.invalid/book", chapter=1, start=0, end=5),
                voice_intent=VoiceIntent(preferred_profile="alice"),
            ),
            Utterance(
                id="u-0001",
                order=1,
                text="beta",
                segment_type=SegmentType.DIALOGUE,
                speaker_id="bob",
                source_ref=SourceRef(
                    url="https://example.invalid/book", chapter=1, start=6, end=10
                ),
                voice_intent=VoiceIntent(preferred_profile="bob"),
            ),
            Utterance(
                id="u-0002",
                order=2,
                text="gamma",
                segment_type=SegmentType.NARRATION,
                source_ref=SourceRef(
                    url="https://example.invalid/book", chapter=1, start=11, end=16
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_playback_queue_preserves_plan_order_with_lookahead() -> None:
    provider = FakeProvider(delay=0.01)
    service = SynthesisService(provider)
    played: list[bytes] = []
    queue = UtterancePlaybackQueue(service, lookahead=3, play_audio=played.append)

    results = await queue.play_plan(make_plan())

    assert [value.audio_data for value in results] == [
        _audio_for("alpha"),
        _audio_for("beta"),
        _audio_for("gamma"),
    ]
    assert played == [_audio_for("alpha"), _audio_for("beta"), _audio_for("gamma")]
    assert [item.utterance_id for item in provider.calls] == ["u-0000", "u-0001", "u-0002"]
    assert provider.closed is False


@pytest.mark.asyncio
async def test_unknown_resume_id_falls_back_to_start_order() -> None:
    provider = FakeProvider()
    service = SynthesisService(provider)
    queue = UtterancePlaybackQueue(service, play_audio=lambda _data: None)

    await queue.play_plan(make_plan(), start_utterance_id="missing", start_order=1)

    assert [item.utterance_id for item in provider.calls] == ["u-0001", "u-0002"]


def test_stop_is_safe_without_running_event_loop() -> None:
    queue = UtterancePlaybackQueue(object())  # type: ignore[arg-type]

    queue.stop()


def test_cross_thread_stop_is_dispatched_to_owner_loop() -> None:
    provider = FakeProvider(delay=0.2)
    service = SynthesisService(provider)
    queue = UtterancePlaybackQueue(service, lookahead=3, play_audio=lambda _data: None)
    errors: list[BaseException] = []

    def run() -> None:
        async def play() -> None:
            try:
                await queue.play_plan(make_plan())
            except BaseException as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        asyncio.run(play())

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(100):
        if queue._loop is not None:
            break
        time.sleep(0.005)
    assert queue._loop is not None
    queue.stop()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert errors == []


@pytest.mark.asyncio
async def test_read_plan_uses_utterance_voice_intent_and_bookmark_reference(tmp_path: Path) -> None:
    provider = FakeProvider()
    service = SynthesisService(provider)
    played: list[bytes] = []
    bookmarks = BookmarkManager(tmp_path / "bookmarks")
    engine = ReadingEngine(
        FakeTTSManager(provider),
        synthesis_service=service,
        bookmark_manager=bookmarks,
        playback_queue=UtterancePlaybackQueue(service, lookahead=2, play_audio=played.append),
    )

    await engine.read_plan(make_plan(), source_url="https://example.invalid/book")

    assert [item.voice_intent.preferred_voice_profile for item in provider.calls] == [
        "alice",
        "bob",
        None,
    ]
    latest = bookmarks.get_last()
    assert latest is not None
    assert latest.plan_id == "plan-reader"
    assert latest.utterance_id == "u-0002"
    assert latest.source_ref is not None
    assert latest.source_ref.start == 11
    assert service._closed is False


@pytest.mark.asyncio
async def test_f16_reader_scene_and_emotion_change_provider_boundary_parameters(
    tmp_path: Path,
) -> None:
    provider = FakeProvider()
    service = SynthesisService(provider)
    mapper = ParamMapper(
        {
            "scenes": {
                "daily": {"speed": 1.0, "pitch": 0.0},
                "battle": {"speed": 1.2, "pitch": 0.2},
            },
            "emotion_styles": {
                "neutral": {
                    "param_offsets": {"intonation": 1.0},
                    "style_weights": [1.0, 0.0],
                },
                "angry": {
                    "param_offsets": {"intonation": 1.5},
                    "style_weights": [0.0, 1.0],
                },
            },
        }
    )
    plan = UtterancePlan(
        schema_version=1,
        plan_id="scene-parameter-plan",
        source_fingerprint="source",
        config_fingerprint="config",
        utterances=(
            Utterance(
                id="daily",
                order=0,
                text="日常",
                segment_type=SegmentType.NARRATION,
                scene="daily",
                emotion="neutral",
                intensity=0.0,
            ),
            Utterance(
                id="battle",
                order=1,
                text="戦闘",
                segment_type=SegmentType.DIALOGUE,
                scene="battle",
                emotion="angry",
                intensity=1.0,
            ),
        ),
    )
    engine = ReadingEngine(
        FakeTTSManager(provider),
        synthesis_service=service,
        param_mapper=mapper,
        bookmark_manager=BookmarkManager(tmp_path / "bookmarks"),
        playback_queue=UtterancePlaybackQueue(service, play_audio=lambda _data: None),
    )

    await engine.read_plan(plan)

    daily, battle = [request.resolved_parameters for request in provider.calls]
    assert daily is not None and battle is not None
    assert daily.speaking_rate == pytest.approx(1.0)
    assert battle.speaking_rate == pytest.approx(1.2)
    assert daily.intonation == pytest.approx(1.0)
    assert battle.intonation == pytest.approx(1.5)
    assert daily.style_weights != battle.style_weights
    assert battle.style_weights == pytest.approx((0.0, 1.0))
    await service.close()


@pytest.mark.asyncio
async def test_stop_cancels_lookahead_without_orphan_tasks(tmp_path: Path) -> None:
    provider = FakeProvider(delay=1.0)
    service = SynthesisService(provider)
    queue = UtterancePlaybackQueue(service, lookahead=3, play_audio=lambda _data: None)
    engine = ReadingEngine(
        FakeTTSManager(provider),
        synthesis_service=service,
        bookmark_manager=BookmarkManager(tmp_path / "bookmarks"),
        playback_queue=queue,
    )

    task = asyncio.create_task(engine.read_plan(make_plan()))
    for _ in range(100):
        if len(provider.calls) == 3:
            break
        await asyncio.sleep(0.005)
    engine.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert provider.cancelled >= 1
    assert not queue._pending_tasks
    assert not queue.is_running
    assert service._closed is False


@pytest.mark.asyncio
async def test_engine_closes_only_service_it_constructs(tmp_path: Path) -> None:
    provider = LegacyProvider()
    manager = FakeTTSManager(provider)  # type: ignore[arg-type]
    engine = ReadingEngine(
        manager,
        bookmark_manager=BookmarkManager(tmp_path / "bookmarks"),
    )
    service = engine._ensure_synthesis_service()
    engine._playback_queue = UtterancePlaybackQueue(service, play_audio=lambda _data: None)
    engine._owns_playback_queue = True

    await engine.read_plan(make_plan(), source_url="https://example.invalid/book")

    assert provider.closed is True
    assert engine.synthesis_service is None


class FakePlanner:
    def __init__(self, plan: UtterancePlan) -> None:
        self.plan_value = plan

    async def plan(self, _text: str, **_kwargs) -> UtterancePlan:
        return self.plan_value


@pytest.mark.asyncio
async def test_read_url_uses_plan_path_for_multiple_speakers(monkeypatch, tmp_path: Path) -> None:
    from yomiage.reader import engine as engine_module
    from yomiage.sources.base import Chapter

    provider = FakeProvider()
    service = SynthesisService(provider)
    played: list[bytes] = []

    class Source:
        async def fetch_chapter(self, _url: str) -> Chapter:
            return Chapter("chapter", "ignored", "", "https://example.invalid/book", 0)

        async def get_next_chapter_url(self, _url: str) -> str | None:
            return None

    monkeypatch.setattr(
        engine_module.registry,
        "resolve",
        lambda _url, **_kwargs: Source(),
    )
    original_character_db = engine_module.CharacterDB
    monkeypatch.setattr(
        engine_module,
        "CharacterDB",
        lambda _work_id, **_kwargs: original_character_db(
            "test", data_dir=tmp_path / "characters", persist=False
        ),
    )
    engine = ReadingEngine(
        FakeTTSManager(provider),
        synthesis_service=service,
        planning_adapter=FakePlanner(make_plan()),
        bookmark_manager=BookmarkManager(tmp_path / "bookmarks"),
        playback_queue=UtterancePlaybackQueue(service, lookahead=2, play_audio=played.append),
        auto_advance=False,
    )

    await engine.read_url("https://example.invalid/book")

    assert [item.utterance_id for item in provider.calls] == ["u-0000", "u-0001", "u-0002"]
    assert played == [_audio_for("alpha"), _audio_for("beta"), _audio_for("gamma")]
