"""F04/F15 と D02 の playback lifecycle 回帰テスト。"""

from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from yomiage.adapters.tts.legacy import LegacyTTSProviderAdapter
from yomiage.application.synthesis import SynthesisService
from yomiage.core.models import SegmentType, Utterance
from yomiage.core.plan import UtterancePlan
from yomiage.interfaces.cli import slack as slack_cli
from yomiage.reader.playback import UtterancePlaybackQueue
from yomiage.tts.base import AudioResult, TTSParams, TTSProvider
from yomiage.tts.manager import TTSManager
from yomiage.tts.playback import play_wav

from .test_audio_validation import wav_bytes


class _Provider(TTSProvider):
    def __init__(self, *, block: bool = False) -> None:
        super().__init__()
        self.block = block
        self.entered = asyncio.Event()
        self.cancelled = 0
        self.calls = 0
        self.close_calls = 0

    @property
    def name(self) -> str:
        return "fake"

    async def is_available(self) -> bool:
        return True

    async def synthesize(self, _text: str, **_kwargs: object) -> AudioResult:
        self.calls += 1
        self.entered.set()
        try:
            if self.block:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return AudioResult(wav_bytes(), duration=0.01)

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_f04_double_start_is_idempotent_and_drain_leaves_no_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_play(_audio: bytes) -> float:
        return 0.0

    monkeypatch.setattr("yomiage.tts.manager.play_wav", no_play)
    provider = _Provider()
    manager = TTSManager(provider)
    await manager.start()
    first = (manager._synth_task, manager._play_task)
    orphaned: tuple[asyncio.Task | None, asyncio.Task | None] = first
    try:
        await manager.start()
        second = (manager._synth_task, manager._play_task)
        await manager.enqueue("本文", TTSParams())
        await asyncio.wait_for(manager.drain(), timeout=0.1)

        assert second == first
        assert provider.calls == 1
        assert all(task is not None and task.done() for task in second)
    finally:
        for task in {*orphaned, manager._synth_task, manager._play_task}:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in {*orphaned, manager._synth_task, manager._play_task}
              if task is not None),
            return_exceptions=True,
        )
        await manager.close()


@pytest.mark.asyncio
async def test_f04_stop_cancels_blocked_provider_and_returns() -> None:
    provider = _Provider(block=True)
    manager = TTSManager(provider)
    await manager.start()
    await manager.enqueue("停止", TTSParams())
    await provider.entered.wait()

    await asyncio.wait_for(manager.stop(), timeout=0.1)

    assert provider.cancelled == 1
    assert manager._synth_task is not None and manager._synth_task.done()
    assert manager._play_task is not None and manager._play_task.done()
    await manager.close()


def test_f04_slack_entrypoint_starts_manager_and_synthesizes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider()
    manager = TTSManager(provider)

    class _Monitor:
        callback = None

        def on_message(self, callback) -> None:
            self.callback = callback

        async def start(self) -> None:
            assert self.callback is not None
            await self.callback(SimpleNamespace(text="重要な通知"))

    class _Scorer:
        close_calls = 0

        async def score(self, _message: object) -> float:
            return 1.0

        def is_important(self, _score: float) -> bool:
            return True

        async def close(self) -> None:
            self.close_calls += 1

    async def no_play(_audio: bytes) -> float:
        return 0.0

    monitor = _Monitor()
    scorer = _Scorer()
    monkeypatch.setattr("yomiage.tts.manager.play_wav", no_play)
    monkeypatch.setattr(slack_cli.common, "setup_logging", lambda _verbose: None)
    monkeypatch.setattr(slack_cli.common, "load_config", lambda: {})
    monkeypatch.setattr(
        slack_cli.common,
        "create_slack_components",
        lambda _config: (manager, monitor, scorer),
    )

    slack_cli.slack_cmd("start")

    assert provider.calls == 1
    assert scorer.close_calls == 1
    assert provider.close_calls == 1
    assert manager._owned_synthesis_tasks == set()
    assert all(
        task is not None and task.done()
        for task in (manager._synth_task, manager._play_task)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["legacy_manager", "playback_queue"])
async def test_d02_common_playback_lifecycle_cancel_and_close_contract(
    implementation: str,
) -> None:
    provider = _Provider(block=True)
    service = SynthesisService(LegacyTTSProviderAdapter(provider))

    if implementation == "legacy_manager":
        lifecycle = TTSManager(provider, service=service, owns_service=False)
        await lifecycle.start()
        await lifecycle.start()
        await lifecycle.enqueue("停止", TTSParams())
        owner_task = None
    else:
        lifecycle = UtterancePlaybackQueue(service, play_audio=lambda _audio: None)
        plan = UtterancePlan(
            schema_version=1,
            plan_id="lifecycle-plan",
            source_fingerprint="source",
            config_fingerprint="config",
            utterances=(
                Utterance(
                    id="u-0",
                    order=0,
                    text="停止",
                    segment_type=SegmentType.NARRATION,
                ),
            ),
        )
        owner_task = asyncio.create_task(lifecycle.play_plan(plan))

    await provider.entered.wait()
    if implementation == "legacy_manager":
        await asyncio.wait_for(lifecycle.stop(), timeout=0.2)
        assert lifecycle._owned_synthesis_tasks == set()
        assert all(
            task is not None and task.done()
            for task in (lifecycle._synth_task, lifecycle._play_task)
        )
        await lifecycle.close()
    else:
        lifecycle.stop()
        await asyncio.wait_for(owner_task, timeout=0.2)
        assert lifecycle._pending_tasks == set()
        assert lifecycle._active_task is None
        assert lifecycle._current_play_task is None

    assert provider.cancelled == 1
    assert service._closed is False
    assert provider.close_calls == 0
    await service.close()
    assert provider.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["legacy_manager", "playback_queue"])
async def test_d02_common_playback_lifecycle_drains_normally_and_closes_once(
    implementation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider()
    service = SynthesisService(LegacyTTSProviderAdapter(provider))

    async def no_play(_audio: bytes) -> float:
        return 0.0

    monkeypatch.setattr("yomiage.tts.manager.play_wav", no_play)
    if implementation == "legacy_manager":
        lifecycle = TTSManager(provider, service=service, owns_service=False)
        await lifecycle.start()
        await lifecycle.enqueue("正常終了", TTSParams())
        await asyncio.wait_for(lifecycle.drain(), timeout=0.2)
        assert lifecycle._owned_synthesis_tasks == set()
        assert all(
            task is not None and task.done()
            for task in (lifecycle._synth_task, lifecycle._play_task)
        )
        await lifecycle.close()
    else:
        lifecycle = UtterancePlaybackQueue(service, play_audio=lambda _audio: None)
        plan = UtterancePlan(
            schema_version=1,
            plan_id="normal-lifecycle-plan",
            source_fingerprint="source",
            config_fingerprint="config",
            utterances=(
                Utterance(
                    id="u-0",
                    order=0,
                    text="正常終了",
                    segment_type=SegmentType.NARRATION,
                ),
            ),
        )
        await lifecycle.play_plan(plan)
        assert lifecycle._pending_tasks == set()
        assert lifecycle._active_task is None
        assert lifecycle._current_play_task is None

    assert provider.calls == 1
    assert service._closed is False
    assert provider.close_calls == 0
    await service.close()
    assert provider.close_calls == 1


@pytest.mark.asyncio
async def test_f15_sounddevice_cancel_stops_device_and_joins_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    released = threading.Event()
    finished = threading.Event()
    stop_calls = 0
    play_calls = 0
    wait_calls = 0

    async def no_aplay(*_args: object, **_kwargs: object):
        raise FileNotFoundError

    def wait() -> None:
        nonlocal wait_calls
        wait_calls += 1
        entered.set()
        if wait_calls == 1:
            released.wait(timeout=1.0)
        finished.set()

    def stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        released.set()

    def play(*_args: object) -> None:
        nonlocal play_calls
        play_calls += 1

    fake_sd = SimpleNamespace(play=play, wait=wait, stop=stop)
    fake_np = SimpleNamespace(
        int8="int8",
        int16="int16",
        int32="int32",
        frombuffer=lambda data, dtype: SimpleNamespace(reshape=lambda *_shape: data),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_aplay)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)
    task = asyncio.create_task(play_wav(wav_bytes()))
    try:
        await asyncio.sleep(0)
        assert entered.wait(timeout=0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert stop_calls == 1
        assert finished.wait(timeout=0.2)

        next_duration = await asyncio.wait_for(play_wav(wav_bytes()), timeout=0.2)
        assert next_duration > 0
        assert play_calls == 2
        assert wait_calls == 2
        assert stop_calls == 1
    finally:
        released.set()
