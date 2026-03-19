"""TTS Manager — provider selection, fallback, and synthesis queue."""

import asyncio
import time

from loguru import logger

from .base import AudioResult, TTSParams, TTSProvider
from .playback import play_wav


class TTSManager:
    """Manages TTS providers with fallback and a lookahead synthesis queue."""

    def __init__(
        self,
        primary: TTSProvider,
        fallback: TTSProvider | None = None,
        lookahead: int = 3,
    ):
        self.primary = primary
        self.fallback = fallback
        self.lookahead = lookahead
        self._queue: asyncio.Queue[tuple[str, TTSParams] | None] = asyncio.Queue()
        self._audio_queue: asyncio.Queue[AudioResult | None] = asyncio.Queue()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused
        self._stop_event = asyncio.Event()
        self._synth_task: asyncio.Task | None = None
        self._play_task: asyncio.Task | None = None

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

    async def synthesize_immediate(
        self, text: str, params: TTSParams | None = None
    ) -> AudioResult:
        """Synthesize immediately without queueing."""
        p = params or TTSParams()
        return await self._do_synthesize(text, p)

    def _build_kwargs(self, params: TTSParams) -> dict:
        kwargs = {}
        if params.pitch:
            kwargs["pitch"] = params.pitch
        if params.volume:
            kwargs["volume"] = params.volume
        if params.intonation != 1.0:
            kwargs["intonation"] = params.intonation
        if params.huskiness:
            kwargs["huskiness"] = params.huskiness
        if params.alp:
            kwargs["alp"] = params.alp
        if params.style_weights:
            kwargs["style_weights"] = params.style_weights
        if params.voice_id:
            kwargs["voice_id"] = params.voice_id
        return kwargs

    async def _do_synthesize(self, text: str, params: TTSParams) -> AudioResult:
        provider = self._select_provider()
        kwargs = self._build_kwargs(params)

        try:
            return await provider.synthesize(text, speed=params.speed, **kwargs)
        except Exception as e:
            if self.fallback and provider is not self.fallback:
                logger.warning(
                    f"{provider.name} failed ({e}), using {self.fallback.name}"
                )
                return await self.fallback.synthesize(
                    text, speed=params.speed, **kwargs
                )
            raise

    def _select_provider(self) -> TTSProvider:
        if self.primary.healthy:
            return self.primary
        if self.fallback:
            logger.warning(f"{self.primary.name} unhealthy, using {self.fallback.name}")
            return self.fallback
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
                text, params = item
                pending.append(asyncio.create_task(self._safe_synthesize(text, params)))

            if not pending:
                # Nothing in queue yet, wait for next item
                item = await self._queue.get()
                if item is None:
                    await self._audio_queue.put(None)
                    return
                text, params = item
                pending.append(asyncio.create_task(self._safe_synthesize(text, params)))

            # Wait for the first (oldest) task to complete — preserves order
            result = await pending.pop(0)
            await self._audio_queue.put(result)

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

            kwargs = self._build_kwargs(params)
            synth_coro = self._select_provider().synthesize(
                text, speed=params.speed, **kwargs
            )

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
                logger.error(f"VoiSona synthesis failed: {e}")
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
