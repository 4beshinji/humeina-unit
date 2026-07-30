"""TTS Manager — provider selection, fallback, and synthesis queue."""

from __future__ import annotations

import asyncio
import io
import time
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from .base import AudioResult, TTSParams, TTSProvider
from .playback import play_wav

if TYPE_CHECKING:
    from ..api.hooks import EventHooks
    from ..api.metrics import MetricsCollector
    from ..application.synthesis import SynthesisService
    from ..tools.voice_profile import VoiceProfile


@dataclass
class RawAudioItem:
    """事前収録WAVをそのまま再生キューに流すためのセンチネル."""
    audio_data: bytes


class TTSManager:
    """Manages TTS providers with fallback and a lookahead synthesis queue."""

    def __init__(
        self,
        primary: TTSProvider,
        fallback: TTSProvider | None = None,
        lookahead: int = 3,
        voice_profile: "VoiceProfile | None" = None,
        hooks: "EventHooks | None" = None,
        metrics: "MetricsCollector | None" = None,
        service: "SynthesisService | None" = None,
        owns_service: bool = False,
    ):
        self.primary = primary
        self.fallback = fallback
        self.lookahead = lookahead
        self.voice_profile = voice_profile
        self._hooks = hooks
        self._metrics = metrics
        # TTSManager is retained as a queue/playback compatibility facade for Reader/News.
        # Retry, fallback, cache, timeout, and provider lifecycle are owned by the canonical
        # application SynthesisService.  A service can be injected by bootstrap; direct legacy
        # construction lazily adapts its providers on first synthesis.
        self._service = service
        self._owns_service = bool(owns_service and service is not None)
        self._closed_owned_service = False
        if service is not None:
            self._attach_metrics(service)
        self._queue: asyncio.Queue[tuple[str, TTSParams] | RawAudioItem | None] = asyncio.Queue()
        self._audio_queue: asyncio.Queue[AudioResult | None] = asyncio.Queue()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused
        self._stop_event = asyncio.Event()
        self._synth_task: asyncio.Task | None = None
        self._play_task: asyncio.Task | None = None

    @property
    def service(self) -> "SynthesisService | None":
        """Canonical synthesis service used by this compatibility facade."""

        return self._service

    @property
    def owns_service(self) -> bool:
        """Whether closing this facade also closes its providers."""

        return self._owns_service

    @property
    def providers_closed(self) -> bool:
        """Whether an owned service already closed the legacy providers."""

        return self._closed_owned_service

    def _attach_metrics(self, service: "SynthesisService") -> None:
        """Attach a collector only when a supplied service has no collector."""

        if self._metrics is None or getattr(service, "metrics", None) is not None:
            return
        try:
            service.metrics = self._metrics
        except (AttributeError, TypeError):
            # Keep facade compatibility with third-party read-only service ports.
            return

    def _ensure_service(self) -> "SynthesisService":
        """Build the canonical service once for legacy direct construction."""

        if self._service is None:
            # Import lazily to avoid a bootstrap -> manager import cycle during composition.
            from ..bootstrap import create_synthesis_service

            providers: dict[str, Any] = {self.primary.name: self.primary}
            if self.fallback is not None and self.fallback.name != self.primary.name:
                providers[self.fallback.name] = self.fallback
            self._service = create_synthesis_service(
                providers,
                primary=self.primary.name,
                fallback=self.fallback.name if self.fallback is not None else None,
                metrics=self._metrics,
            )
            self._owns_service = True
        return self._service

    async def start(self) -> None:
        """Start the synthesis and playback worker loops."""
        self._stop_event.clear()
        self._synth_task = asyncio.create_task(self._synth_loop())
        self._play_task = asyncio.create_task(self._play_loop())

    async def drain(self) -> None:
        """Wait for all queued items to be processed, then stop workers."""
        await self._queue.put(None)  # sentinel → synth_loop exits → play_loop exits
        if self._synth_task:
            await self._synth_task
        if self._play_task:
            await self._play_task

    async def stop(self) -> None:
        """Stop all workers immediately and clear queues."""
        self._stop_event.set()
        await self._queue.put(None)
        await self._audio_queue.put(None)
        if self._synth_task:
            await self._synth_task
        if self._play_task:
            await self._play_task
        self._clear_queues()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def _clear_queues(self) -> None:
        for q in (self._queue, self._audio_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

    async def enqueue(self, text: str, params: TTSParams | None = None) -> None:
        """Add a text segment to the synthesis queue."""
        await self._queue.put((text, params or TTSParams()))

    async def enqueue_raw_audio(self, audio_data: bytes) -> None:
        """事前収録WAVをそのまま再生キューに挿入する.

        合成ループをバイパスし、enqueue() の呼び出し順序で
        _synth_loop_default の pending キューに積まれるため順序が保証される。
        低速プロバイダー（pipelined）では効果なし（呼び出し側でガードすること）。
        """
        await self._queue.put(RawAudioItem(audio_data=audio_data))

    async def synthesize_immediate(
        self, text: str, params: TTSParams | None = None
    ) -> AudioResult:
        """Synthesize immediately without queueing."""
        p = params or TTSParams()
        return await self._do_synthesize(text, p)

    def _build_kwargs(self, params: TTSParams) -> dict:
        kwargs: dict = {}

        # VoiceProfile からベースパラメータを計算
        if self.voice_profile and params.preset:
            profile_params = self.voice_profile.compute_params(
                preset=params.preset,
                emotion=params.emotion,
                intensity=params.intensity,
            )
            kwargs.update(profile_params)

        # TTSParams で上書き
        kwargs["speed"] = params.speed
        if params.voice_id:
            kwargs["voice_id"] = params.voice_id
        if params.pitch != 0.0:
            kwargs["pitch"] = params.pitch
        if params.volume != 0.0:
            kwargs["volume"] = params.volume
        if params.intonation != 1.0:
            kwargs["intonation"] = params.intonation
        if params.huskiness != 0.0:
            kwargs["huskiness"] = params.huskiness
        if params.alp != 0.0:
            kwargs["alp"] = params.alp
        if params.style_weights:
            kwargs["style_weights"] = params.style_weights
        if params.extra:
            kwargs.update(params.extra)
        return kwargs

    async def _do_synthesize(self, text: str, params: TTSParams) -> AudioResult:
        kwargs = self._build_kwargs(params)
        engine_name = self._select_provider().name

        if self._hooks:
            self._hooks.emit_synthesis_start(text, engine_name, kwargs)

        start = asyncio.get_event_loop().time()
        try:
            from ..application.synthesis import SynthesisRequest
            from ..core.models import VoiceIntent

            try:
                intent = VoiceIntent(
                    speaking_rate=float(kwargs.get("speed", 1.0)),
                    pitch=float(kwargs.get("pitch", 0.0)),
                    volume=float(kwargs.get("volume", 0.0)),
                    intonation=float(kwargs.get("intonation", 1.0)),
                    emotion=str(kwargs.get("emotion", "neutral")),
                    intensity=max(0.0, min(1.0, float(kwargs.get("intensity", 0.5)))),
                    preferred_voice_profile=(
                        str(kwargs["preset"]) if kwargs.get("preset") is not None else None
                    ),
                )
            except (TypeError, ValueError):
                intent = VoiceIntent()
            result = await self._ensure_service().synthesize(
                SynthesisRequest(text=text, voice_intent=intent, metadata=kwargs)
            )
            duration_ms = (asyncio.get_event_loop().time() - start) * 1000
            if self._hooks:
                self._hooks.emit_synthesis_end(
                    text, engine_name, kwargs, duration_ms, cache_hit=False
                )
            return AudioResult(
                audio_data=result.audio_data or b"",
                format=result.format,
                sample_rate=result.sample_rate,
                duration=result.duration,
            )
        except Exception as e:
            if self._hooks:
                self._hooks.emit_synthesis_error(
                    text, engine_name, kwargs, str(e)
                )
            raise

    def _select_provider(self) -> TTSProvider:
        # Provider selection/fallback is owned by SynthesisService.  This method remains as a
        # Reader compatibility hook for choosing playback strategy and never executes a second
        # fallback branch.
        return self.primary

    def _is_slow_provider(self) -> bool:
        return self._select_provider().is_slow

    async def _synth_loop(self) -> None:
        if self._is_slow_provider():
            await self._synth_loop_pipelined()
        else:
            await self._synth_loop_default()

    async def _synth_loop_default(self) -> None:
        """VOICEVOX等: lookahead数のプリフェッチで合成してaudio_queueに入れる.

        VOICEVOX is fast (local Docker), so we prefetch up to `lookahead`
        items concurrently to keep the audio_queue populated.
        RawAudioItem はパススルータスクとして pending に積まれ順序が保証される。
        """
        pending: list[asyncio.Task] = []

        while not self._stop_event.is_set():
            # Fill up to lookahead pending tasks
            while len(pending) < self.lookahead:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is None:
                    # Sentinel: wait for pending, then exit
                    for task in pending:
                        result = await task
                        await self._audio_queue.put(result)
                    await self._audio_queue.put(None)
                    return
                if isinstance(item, RawAudioItem):
                    pending.append(asyncio.create_task(self._passthrough(item)))
                else:
                    text, params = item
                    pending.append(asyncio.create_task(self._safe_synthesize(text, params)))

            if not pending:
                # Nothing in queue yet, wait for next item
                item = await self._queue.get()
                if item is None:
                    await self._audio_queue.put(None)
                    return
                if isinstance(item, RawAudioItem):
                    pending.append(asyncio.create_task(self._passthrough(item)))
                else:
                    text, params = item
                    pending.append(asyncio.create_task(self._safe_synthesize(text, params)))

            # Wait for the first (oldest) task to complete — preserves order
            result = await pending.pop(0)
            await self._audio_queue.put(result)

    @staticmethod
    async def _passthrough(item: RawAudioItem) -> AudioResult:
        """RawAudioItemをAudioResultに変換（合成不要）."""
        try:
            with wave.open(io.BytesIO(item.audio_data), "rb") as wf:
                duration = wf.getnframes() / wf.getframerate()
        except Exception:
            duration = 0.0
        return AudioResult(audio_data=item.audio_data, duration=duration)

    async def _safe_synthesize(self, text: str, params: TTSParams) -> AudioResult:
        try:
            return await self._do_synthesize(text, params)
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return AudioResult(audio_data=b"", duration=0.0)

    async def _synth_loop_pipelined(self) -> None:
        """低速プロバイダー用パイプライン合成。

        チャンクNの再生中にチャンクN+1の合成を開始し、
        再生が途切れないようにする。順序はこちら側で厳密に管理。
        """
        count = 0
        wall_start = time.monotonic()
        prev_play_duration = 0.0

        while not self._stop_event.is_set():
            item = await self._queue.get()
            if item is None:
                # 最後のチャンクの再生完了を待つ
                if prev_play_duration > 0:
                    await asyncio.sleep(prev_play_duration)
                break

            text, params = item
            if not text.strip():
                continue

            synth_coro = self._do_synthesize(text, params)

            try:
                if prev_play_duration > 0:
                    # 前チャンク再生 と 現チャンク合成 を同時実行
                    result, _ = await asyncio.gather(
                        synth_coro,
                        asyncio.sleep(prev_play_duration),
                    )
                else:
                    result = await synth_coro
            except Exception as e:
                logger.error(f"Slow-provider synthesis failed: {e}")
                if prev_play_duration > 0:
                    await asyncio.sleep(prev_play_duration)
                prev_play_duration = 0.0
                continue

            count += 1
            prev_play_duration = result.duration or 0.0

        wall_elapsed = time.monotonic() - wall_start
        if count:
            logger.info(
                f"VoiSona complete: {count} chunks "
                f"(wall {wall_elapsed:.1f}s)"
            )

        await self._audio_queue.put(None)

    async def _play_loop(self) -> None:
        while not self._stop_event.is_set():
            result = await self._audio_queue.get()
            if result is None:
                break
            await self._pause_event.wait()
            if self._stop_event.is_set():
                break
            if result.audio_data:
                try:
                    await play_wav(result.audio_data)
                except Exception as e:
                    logger.error(f"Playback failed: {e}")
            elif result.duration and result.duration > 0:
                # VoiSona: audio played via SPICE, just wait for duration
                await asyncio.sleep(result.duration)

    async def close(self) -> None:
        """Stop worker tasks and close resources owned by this facade."""

        if any(
            task is not None and not task.done()
            for task in (self._synth_task, self._play_task)
        ):
            await self.stop()
        if self._owns_service and self._service is not None:
            await self._service.close()
            self._service = None
            self._owns_service = False
            self._closed_owned_service = True
