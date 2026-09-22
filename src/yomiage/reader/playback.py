"""Utterance単位の合成・再生キュー。

旧 ``TTSManager`` は provider の都合に合わせた queue で、チャンク投入後に
``dominant`` なセグメントを再利用していた。ここでは発話計画を入力に取り、合成を
lookahead させつつ、再生と完了通知は必ず計画順に行う。キュー自身は
``SynthesisService`` の所有権を持たないため、外部から注入されたserviceを勝手に
closeしない。
"""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..application.synthesis import SynthesisRequest, SynthesisResult, SynthesisService
from ..application.voice_parameters import VoiceResolutionContext
from ..core.models import SegmentType, Utterance
from ..core.plan import UtterancePlan
from ..tts.playback import play_wav

AudioPlayer = Callable[[bytes], Awaitable[float | None] | float | None]
UtteranceCallback = Callable[[Utterance, SynthesisResult], Awaitable[None] | None]
RequestMetadataFactory = Callable[[Utterance], Mapping[str, Any]]
DestinationFactory = Callable[[Utterance], str | None]
ParameterContextFactory = Callable[[Utterance], VoiceResolutionContext]


@dataclass(slots=True)
class _PendingSynthesis:
    utterance: Utterance
    task: asyncio.Task[SynthesisResult]


class UtterancePlaybackQueue:
    """UtterancePlanをlookahead合成し、古い発話から順に再生する。

    ``play_plan`` は一つの管理タスクとして動作する。合成失敗・外部キャンセル・
    ``stop`` のいずれでも pending task をすべて cancel/gather してから戻るため、
    先読みの孤児taskを残さない。
    """

    def __init__(
        self,
        service: SynthesisService,
        *,
        lookahead: int = 3,
        play_audio: AudioPlayer | None = None,
    ) -> None:
        if isinstance(lookahead, bool) or lookahead <= 0:
            raise ValueError("lookahead must be a positive integer")
        self.service = service
        self.lookahead = int(lookahead)
        self.play_audio = play_audio or play_wav

        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active_task: asyncio.Task[Any] | None = None
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._current_play_task: asyncio.Task[Any] | None = None

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def is_running(self) -> bool:
        return self._active_task is not None and not self._active_task.done()

    def pause(self) -> None:
        """現在の発話を止めず、次の発話境界で再生を待機する。"""

        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def stop(self) -> None:
        """再生を停止し、先読み合成と再生管理taskをキャンセルする。"""

        owner_loop = self._loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if owner_loop is not None and owner_loop.is_running() and owner_loop is not current_loop:
            # asyncio.Event/taskはowner loop以外から直接触らない。callback内で同じloopの
            # private helperを呼ぶため、cross-thread stopはここで即returnする。
            owner_loop.call_soon_threadsafe(self._stop_on_owner_loop)
            return
        self._stop_on_owner_loop()

    def _stop_on_owner_loop(self) -> None:
        """owner event loop上でstop状態を更新する。"""

        self._stop_event.set()
        self._pause_event.set()
        try:
            current = asyncio.current_task()
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current = None
            current_loop = None

        def cancel_task(task: asyncio.Task[Any]) -> None:
            if task.done():
                return
            loop = self._loop
            if loop is not None and loop.is_running() and loop is not current_loop:
                loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()

        for task in tuple(self._pending_tasks):
            cancel_task(task)
        # stop()は通常read_planとは別の制御taskから呼ばれる。外部からの停止なら
        # play_audio/sleep中の管理taskも起こし、同じtask内の呼出しなら次の境界で脱出する。
        active = self._active_task
        if active is not None and active is not current and not active.done():
            cancel_task(active)
        playing = self._current_play_task
        if playing is not None and playing is not current and not playing.done():
            cancel_task(playing)

    async def play_plan(
        self,
        plan: UtterancePlan,
        *,
        provider: str | None = None,
        work_id: str | None = None,
        run_id: str | None = None,
        start_utterance_id: str | None = None,
        start_order: int = 0,
        on_played: UtteranceCallback | None = None,
        metadata_factory: RequestMetadataFactory | None = None,
        destination_factory: DestinationFactory | None = None,
        parameter_context_factory: ParameterContextFactory | None = None,
    ) -> list[SynthesisResult]:
        """計画を再生し、再生済み結果を順番に返す。

        scene breakは計画警告だけで、serviceへ合成要求を送らない。``start_utterance_id``
        が見つからない場合は安全側に倒して先頭（``start_order``）から再開する。
        """

        if not isinstance(plan, UtterancePlan):
            raise TypeError("plan must be UtterancePlan")
        if self.is_running:
            raise RuntimeError("playback queue is already running")

        utterances = [
            item
            for item in plan.ordered_utterances
            if item.segment_type is not SegmentType.SCENE_BREAK
        ]
        if start_utterance_id is not None:
            found = False
            for index, item in enumerate(utterances):
                if item.id == start_utterance_id:
                    utterances = utterances[index:]
                    found = True
                    break
            if not found and start_order > 0:
                utterances = [item for item in utterances if item.order >= start_order]
        elif start_order > 0:
            utterances = [item for item in utterances if item.order >= start_order]

        self._stop_event.clear()
        self._pause_event.set()
        self._pending_tasks.clear()
        self._loop = asyncio.get_running_loop()
        self._active_task = asyncio.current_task()
        pending: deque[_PendingSynthesis] = deque()
        results: list[SynthesisResult] = []
        iterator = iter(utterances)

        def schedule(item: Utterance) -> None:
            metadata = dict(metadata_factory(item) if metadata_factory else {})
            destination = destination_factory(item) if destination_factory else None
            request = SynthesisRequest.from_utterance(
                item,
                work_id=work_id or plan.plan_id,
                run_id=run_id,
                destination=destination,
            )
            if provider is not None:
                request = SynthesisRequest(
                    text=request.text,
                    voice_intent=request.voice_intent,
                    utterance_id=request.utterance_id,
                    provider=provider,
                    work_id=request.work_id,
                    run_id=request.run_id,
                    scene=request.scene,
                    parameter_context=request.parameter_context,
                    destination=request.destination,
                    target=request.target,
                    metadata=request.metadata,
                )
            if parameter_context_factory is not None:
                request = SynthesisRequest(
                    text=request.text,
                    voice_intent=request.voice_intent,
                    utterance_id=request.utterance_id,
                    provider=request.provider,
                    work_id=request.work_id,
                    run_id=request.run_id,
                    scene=request.scene,
                    parameter_context=parameter_context_factory(item),
                    destination=request.destination,
                    target=request.target,
                )
            if metadata:
                request = SynthesisRequest(
                    text=request.text,
                    voice_intent=request.voice_intent,
                    utterance_id=request.utterance_id,
                    provider=request.provider,
                    work_id=request.work_id,
                    run_id=request.run_id,
                    scene=request.scene,
                    parameter_context=request.parameter_context,
                    destination=request.destination,
                    target=request.target,
                    metadata=metadata,
                )
            task = asyncio.create_task(self.service.synthesize(request))
            self._pending_tasks.add(task)
            pending.append(_PendingSynthesis(item, task))

        try:
            exhausted = False
            while pending or not exhausted:
                while not exhausted and len(pending) < self.lookahead:
                    try:
                        schedule(next(iterator))
                    except StopIteration:
                        exhausted = True
                if not pending:
                    break

                item = pending.popleft()
                try:
                    result = await item.task
                finally:
                    self._pending_tasks.discard(item.task)
                if hasattr(result, "is_success") and not result.is_success:
                    raise RuntimeError(
                        f"synthesis failed for utterance {item.utterance.id}: "
                        f"{getattr(result, 'status', 'unknown')}"
                    )
                if self._stop_event.is_set():
                    break

                await self._pause_event.wait()
                if self._stop_event.is_set():
                    break
                await self._play_result(result)
                results.append(result)
                if on_played is not None:
                    callback_result = on_played(item.utterance, result)
                    if inspect.isawaitable(callback_result):
                        await callback_result
        except asyncio.CancelledError:
            # stop()によるキャンセルは正常な停止として扱う。caller自身のcancelは再送出し、
            # TaskSupervisor等の上位が中断状態を記録できるようにする。
            if not self._stop_event.is_set():
                raise
        except Exception:
            self._stop_event.set()
            raise
        finally:
            await self._cancel_pending(pending)
            self._active_task = None
            self._current_play_task = None
            self._loop = None
            self._stop_event.clear()
            self._pause_event.set()
        return results

    async def _play_result(self, result: SynthesisResult) -> None:
        if result.audio_data:
            task = asyncio.create_task(self._invoke_player(result.audio_data))
            self._current_play_task = task
            try:
                await task
            finally:
                self._current_play_task = None
            return
        duration = float(result.duration or 0.0)
        if duration > 0:
            await self._wait_duration(duration)

    async def _invoke_player(self, audio_data: bytes) -> None:
        value = self.play_audio(audio_data)
        if inspect.isawaitable(value):
            await value

    async def _wait_duration(self, duration: float) -> None:
        """audio_dataを返さないVoiSona remote再生をpause/stop可能に待つ。"""

        loop = asyncio.get_running_loop()
        remaining = duration
        while remaining > 0 and not self._stop_event.is_set():
            await self._pause_event.wait()
            if self._stop_event.is_set():
                return
            started = loop.time()
            await asyncio.sleep(min(remaining, 0.05))
            if self._pause_event.is_set():
                remaining -= loop.time() - started

    async def _cancel_pending(self, pending: Iterable[_PendingSynthesis]) -> None:
        tasks = {item.task for item in pending}
        tasks.update(self._pending_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending_tasks.clear()


__all__ = ["UtterancePlaybackQueue"]
