"""Reading engine — main orchestrator for text-to-speech reading."""

import asyncio
import hashlib
import re
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from ..adapters.tts import LegacyTTSProviderAdapter, VoisonaFileProviderAdapter
from ..application.planning import ReaderPlanningAdapter, UtterancePlanner
from ..application.synthesis import SynthesisService
from ..core.models import SourceRef, Utterance, VoiceIntent
from ..core.plan import UtterancePlan
from ..exvoice.catalog import normalize_wav
from ..exvoice.manager import ExVoiceManager
from ..nlp.classifier import TextClassifier
from ..nlp.pipeline import NLPAnalyzer
from ..nlp.scene_analyzer import AnalyzedSegment, SceneAnalyzer
from ..nlp.speaker import SpeakerExtractor
from ..nlp.splitter import Chunk, TextSplitter
from ..nlp.text_processor import TextProcessor
from ..ports.sources import URLPolicy
from ..sources import registry
from ..sources.base import Chapter, ContentSource
from ..tts.audio_utils import concat_wav_files
from ..tts.base import TTSParams
from ..tts.manager import TTSManager
from ..tts.playback import play_wav
from .bookmark import Bookmark, BookmarkManager
from .character_db import CharacterDB
from .param_mapper import ParamMapper
from .playback import UtterancePlaybackQueue


class ReadingEngine:
    """メイン読み上げオーケストレーター.

    URL → ソース判別 → チャプター取得 → テキスト前処理 → 分割
    → NLPパイプライン（分類→話者識別→シーン分析）
    → パラメータマッピング → TTS合成・再生
    """

    def __init__(
        self,
        tts_manager: TTSManager,
        text_processor: TextProcessor | None = None,
        splitter: TextSplitter | None = None,
        classifier: TextClassifier | None = None,
        speaker_extractor: SpeakerExtractor | None = None,
        scene_analyzer: SceneAnalyzer | None = None,
        param_mapper: ParamMapper | None = None,
        bookmark_manager: BookmarkManager | None = None,
        auto_advance: bool = True,
        lookahead_chunks: int = 5,
        vm_mount: str = "Z:",
        output_dir: str = "output",
        synth_concurrency: int = 2,
        ex_voice: ExVoiceManager | None = None,
        synthesis_service: SynthesisService | None = None,
        planning_adapter: ReaderPlanningAdapter | None = None,
        playback_queue: UtterancePlaybackQueue | None = None,
        url_policy: URLPolicy | None = None,
    ):
        self.tts = tts_manager
        self.processor = text_processor or TextProcessor()
        self.splitter = splitter or TextSplitter()
        self.classifier = classifier or TextClassifier()
        self.speaker_extractor = speaker_extractor or SpeakerExtractor()
        self.scene_analyzer = scene_analyzer
        self.param_mapper = param_mapper or ParamMapper()
        self.bookmarks = bookmark_manager or BookmarkManager()
        self.auto_advance = auto_advance
        self.lookahead_chunks = lookahead_chunks
        self.vm_mount = vm_mount
        self.output_dir = Path(output_dir)
        self.synth_concurrency = synth_concurrency
        self.ex_voice = ex_voice
        self.synthesis_service = synthesis_service
        self._owns_synthesis_service = False
        self._planning_adapter = planning_adapter
        self._playback_queue = playback_queue
        self._owns_playback_queue = False
        self.url_policy = url_policy

        self._nlp_analyzer = NLPAnalyzer(
            max_chunk_chars=self.splitter.max_chars,
            text_processor=self.processor,
            splitter=self.splitter,
            classifier=self.classifier,
            speaker_extractor=self.speaker_extractor,
            scene_analyzer=self.scene_analyzer,
        )

        self._running = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_event = asyncio.Event()
        self._current_source: ContentSource | None = None
        self._current_chapter: Chapter | None = None
        self._current_chunks: list[Chunk] = []
        self._current_chunk_idx: int = 0
        self._current_plan: UtterancePlan | None = None
        self._current_utterance_idx: int = 0
        self._character_db: CharacterDB | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def current_status(self) -> dict:
        return {
            "running": self._running,
            "paused": self.is_paused,
            "chapter": self._current_chapter.title if self._current_chapter else None,
            "chunk": self._current_chunk_idx,
            "total_chunks": len(self._current_chunks),
            "plan_id": self._current_plan.plan_id if self._current_plan else None,
            "utterance": self._current_utterance_idx,
            "total_utterances": (
                len(self._current_plan.utterances) if self._current_plan else 0
            ),
        }

    async def read_url(
        self,
        url: str,
        start_chunk: int = 0,
        *,
        start_utterance_id: str | None = None,
    ) -> None:
        """URLから読み上げを開始する。

        旧 ``start_chunk`` 引数は互換のため残す。新経路では計画生成後に
        ``start_utterance_id`` を優先し、chunk再分割に依存しない再開を行う。
        """
        self._stop_event.clear()
        self._running = True

        # 作品IDを生成（URLハッシュ）
        work_id = hashlib.md5(url.encode()).hexdigest()[:12]
        self._character_db = CharacterDB(work_id)

        try:
            self._ensure_synthesis_service()
            source = registry.resolve(url, policy=self.url_policy)
            self._current_source = source
            await self._read_from_source(
                source,
                url,
                start_chunk,
                start_utterance_id=start_utterance_id,
            )
        except Exception as e:
            logger.error(f"Reading failed: {e}")
            raise
        finally:
            await self._close_owned_synthesis_service()
            self._running = False

    async def resume_last(self) -> None:
        """最後のブックマークから再開."""
        bookmark = self.bookmarks.get_last()
        if not bookmark:
            logger.warning("No bookmark found to resume from")
            return

        logger.info(
            f"Resuming: {bookmark.work_title or bookmark.source_url} "
            f"ch={bookmark.chapter_index} utterance={bookmark.utterance_id or bookmark.chunk_index}"
        )
        await self.read_url(
            bookmark.chapter_url,
            start_chunk=bookmark.chunk_index,
            start_utterance_id=bookmark.utterance_id,
        )

    def pause(self) -> None:
        self._pause_event.clear()
        self.tts.pause()
        if self._playback_queue is not None:
            self._playback_queue.pause()
        logger.info("Reading paused")

    def resume(self) -> None:
        self._pause_event.set()
        self.tts.resume()
        if self._playback_queue is not None:
            self._playback_queue.resume()
        logger.info("Reading resumed")

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        if self._playback_queue is not None:
            self._playback_queue.stop()
        else:
            # 旧TTSManager.stopはasync API。read_urlのPlan経路ではqueueが必ず先に生成
            # されるが、source fetch中のstopでもリソースを取りこぼさない。
            result = self.tts.stop()
            if asyncio.iscoroutine(result):
                try:
                    asyncio.get_running_loop().create_task(result)
                except RuntimeError:
                    result.close()
        logger.info("Reading stopped")

    async def _read_from_source(
        self,
        source: ContentSource,
        url: str,
        start_chunk: int = 0,
        *,
        start_utterance_id: str | None = None,
    ) -> None:
        chapter_url = url

        while chapter_url and not self._stop_event.is_set():
            chapter = await source.fetch_chapter(chapter_url)
            self._current_chapter = chapter
            logger.info(f"Reading: {chapter.title}")

            # テキスト前処理
            clean_text = self.processor.process(chapter.text)
            if not clean_text:
                logger.warning(f"Empty chapter: {chapter.title}")
                break

            # UtterancePlanを先に確定し、TTS制限による後段処理へ渡す。旧dominant
            # segment経路は互換用のprivate helperとして残すが、通常入口からは呼ばない。
            plan = await self._plan_text(
                clean_text,
                source_url=chapter.source_url,
                chapter_index=chapter.chapter_index,
            )
            await self._read_plan_for_chapter(
                plan,
                chapter=chapter,
                chapter_url=chapter_url,
                start_utterance_id=start_utterance_id,
                start_order=start_chunk,
            )

            if self._stop_event.is_set():
                break

            # 次章への自動遷移
            start_chunk = 0
            start_utterance_id = None
            if self.auto_advance:
                next_url = await source.get_next_chapter_url(chapter_url)
                if next_url:
                    logger.info(f"Advancing to next chapter: {next_url}")
                    chapter_url = next_url
                else:
                    logger.info("No more chapters")
                    break
            else:
                break

    def _ensure_synthesis_service(self) -> SynthesisService:
        """service注入がない旧CLI向けに、provider adapterを組み立てる。"""

        if self.synthesis_service is not None:
            return self.synthesis_service

        def adapt(provider: object):
            # VoiSonaはfile destinationをVM mountへ変換する既存adapterを使う。
            if str(getattr(provider, "name", "")) == "voisona":
                return VoisonaFileProviderAdapter(provider)
            return LegacyTTSProviderAdapter(provider)

        primary = adapt(self.tts.primary)
        fallback = adapt(self.tts.fallback) if self.tts.fallback is not None else None
        providers = {primary.name: primary}
        if fallback is not None and fallback.name != primary.name:
            providers[fallback.name] = fallback
        self.synthesis_service = SynthesisService(
            providers,
            primary=primary.name,
            fallback=fallback.name if fallback is not None else None,
            concurrency=max(1, self.synth_concurrency),
        )
        self._owns_synthesis_service = True
        return self.synthesis_service

    async def _close_owned_synthesis_service(self) -> None:
        service = self.synthesis_service
        if not self._owns_synthesis_service or service is None:
            return
        self._owns_synthesis_service = False
        try:
            await service.close()
        finally:
            self.synthesis_service = None
            if self._owns_playback_queue:
                self._playback_queue = None
                self._owns_playback_queue = False

    def _planner(self) -> ReaderPlanningAdapter:
        if self._planning_adapter is None:
            self._planning_adapter = ReaderPlanningAdapter(UtterancePlanner(self._nlp_analyzer))
        return self._planning_adapter

    async def _plan_text(
        self,
        text: str,
        *,
        source_url: str | None = None,
        chapter_index: int | None = None,
    ) -> UtterancePlan:
        plan = await self._planner().plan(
            text,
            source=source_url,
            chapter=chapter_index,
            known_characters=(
                self._character_db.known_names if self._character_db else None
            ),
            character_db=self._character_db,
        )
        return self._apply_character_voice_intents(plan)

    def _apply_character_voice_intents(self, plan: UtterancePlan) -> UtterancePlan:
        """旧CharacterDBのvoice設定をprovider非依存のVoiceIntentへ投影する。"""

        if self._character_db is None:
            return plan
        changed: list[Utterance] = []
        for utterance in plan.utterances:
            profile = (
                self._character_db.characters.get(utterance.speaker_id)
                if utterance.speaker_id
                else None
            )
            if profile is None:
                changed.append(utterance)
                continue
            base = profile.base_params or {}
            intent = utterance.voice_intent
            # voice_id自体はcoreへ持ち込まず、preferred profileとしてadapterに解決させる。
            preferred = intent.preferred_voice_profile or profile.voice_id
            updated = VoiceIntent(
                archetype=intent.archetype or profile.personality,
                gender=intent.gender or profile.gender,
                age_group=intent.age_group or profile.age_group,
                speaking_rate=base.get("speed", intent.speaking_rate),
                pitch=base.get("pitch", intent.pitch),
                volume=base.get("volume", intent.volume),
                intonation=base.get("intonation", intent.intonation),
                emotion=intent.emotion,
                intensity=intent.intensity,
                preferred_voice_profile=preferred,
            )
            changed.append(replace(utterance, voice_intent=updated))
        return plan.with_utterances(changed)

    async def read_plan(
        self,
        plan: UtterancePlan,
        *,
        source_url: str | None = None,
        chapter_url: str | None = None,
        chapter_index: int = 0,
        title: str = "",
        work_title: str = "",
        start_utterance_id: str | None = None,
        start_order: int = 0,
        run_id: str | None = None,
    ) -> list[Any]:
        """既存source取得を伴わずUtterancePlanを直接読み上げる公開入口。"""

        if not isinstance(plan, UtterancePlan):
            raise TypeError("plan must be UtterancePlan")
        self._stop_event.clear()
        self._running = True
        try:
            self._ensure_synthesis_service()
            return await self._read_plan_for_chapter(
                plan,
                chapter=None,
                chapter_url=chapter_url or source_url,
                source_url=source_url,
                chapter_index=chapter_index,
                title=title,
                work_title=work_title,
                start_utterance_id=start_utterance_id,
                start_order=start_order,
                run_id=run_id,
            )
        finally:
            await self._close_owned_synthesis_service()
            self._running = False

    async def read_text(
        self,
        text: str,
        *,
        source_url: str | None = None,
        chapter_url: str | None = None,
        chapter_index: int = 0,
        title: str = "",
        work_title: str = "",
        start_utterance_id: str | None = None,
        start_order: int = 0,
        run_id: str | None = None,
    ) -> list[Any]:
        """本文を計画化してから読み上げる、URL不要の通常入口。"""

        if not isinstance(text, str):
            raise TypeError("text must be a string")
        clean_text = self.processor.process(text)
        if not clean_text:
            return []
        self._stop_event.clear()
        self._running = True
        work_id = hashlib.md5((source_url or clean_text).encode()).hexdigest()[:12]
        self._character_db = CharacterDB(work_id)
        try:
            self._ensure_synthesis_service()
            plan = await self._plan_text(
                clean_text,
                source_url=source_url,
                chapter_index=chapter_index,
            )
            return await self._read_plan_for_chapter(
                plan,
                chapter=None,
                chapter_url=chapter_url or source_url,
                source_url=source_url,
                chapter_index=chapter_index,
                title=title,
                work_title=work_title,
                start_utterance_id=start_utterance_id,
                start_order=start_order,
                run_id=run_id,
            )
        finally:
            await self._close_owned_synthesis_service()
            self._running = False

    async def _read_plan_for_chapter(
        self,
        plan: UtterancePlan,
        *,
        chapter: Chapter | None,
        chapter_url: str | None,
        source_url: str | None = None,
        chapter_index: int | None = None,
        title: str = "",
        work_title: str = "",
        start_utterance_id: str | None = None,
        start_order: int = 0,
        run_id: str | None = None,
    ) -> list[Any]:
        service = self._ensure_synthesis_service()
        if chapter is not None:
            source_url = source_url or chapter.source_url
            chapter_index = chapter.chapter_index
            title = title or chapter.title
        source_url = source_url or self._source_url_from_plan(plan) or f"plan:{plan.plan_id}"
        chapter_url = chapter_url or source_url
        chapter_index = 0 if chapter_index is None else chapter_index
        title = title or plan.plan_id

        plan = self._apply_character_voice_intents(plan)
        self._current_plan = plan
        self._current_utterance_idx = start_order
        self._current_chunks = []

        queue = self._playback_queue
        if queue is None:
            queue = UtterancePlaybackQueue(
                service,
                lookahead=self.lookahead_chunks,
            )
            self._playback_queue = queue
            self._owns_playback_queue = True

        work_dir: Path | None = None
        metadata_factory = None
        destination_factory = None
        if self.tts._is_slow_provider():
            work_id = hashlib.md5(plan.plan_id.encode()).hexdigest()[:8]
            work_dir = self.output_dir / f"_read_plan_{work_id}_{int(time.time() * 1000)}"
            work_dir.mkdir(parents=True, exist_ok=True)

            def metadata_factory(_item: Utterance) -> Mapping[str, Any]:
                return {"run_dir": str(work_dir), "vm_mount": self.vm_mount}

            def destination_factory(item: Utterance) -> str:
                return str(work_dir / f"{item.order:04d}.wav")

        async def on_played(item: Utterance, _result: Any) -> None:
            self._current_utterance_idx = item.order
            self._current_chunk_idx = item.order
            ref = item.source_ref or SourceRef(url=source_url, chapter=chapter_index)
            self.bookmarks.save(
                Bookmark(
                    source_url=source_url,
                    chapter_url=chapter_url,
                    chapter_index=chapter_index,
                    chunk_index=item.order,
                    title=title,
                    work_title=work_title,
                    plan_id=plan.plan_id,
                    utterance_id=item.id,
                    source_ref=ref,
                )
            )

        try:
            return await queue.play_plan(
                plan,
                work_id=plan.plan_id,
                run_id=run_id,
                start_utterance_id=start_utterance_id,
                start_order=start_order,
                on_played=on_played,
                metadata_factory=metadata_factory,
                destination_factory=destination_factory,
            )
        finally:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _source_url_from_plan(plan: UtterancePlan) -> str | None:
        for item in plan.utterances:
            if item.source_ref and item.source_ref.url:
                return item.source_ref.url
        return None

    async def _read_chunks(
        self,
        chunks: list[Chunk],
        source: ContentSource,
        chapter: Chapter,
        chapter_url: str,
        start_chunk: int = 0,
    ) -> None:
        # NLP先読みバッファ: チャンクバッチをまとめて分析
        analyzed_cache: dict[int, list[AnalyzedSegment]] = {}
        analysis_task: asyncio.Task | None = None
        ex_task: asyncio.Task | None = None

        if self.ex_voice:
            self.ex_voice.reset_chapter()

        for i, chunk in enumerate(chunks):
            if i < start_chunk:
                continue
            if self._stop_event.is_set():
                break

            await self._pause_event.wait()
            self._current_chunk_idx = i

            if chunk.is_scene_break:
                await asyncio.sleep(1.5)
                continue

            if not chunk.text.strip():
                continue

            # 先読み分析をスケジュール
            if i not in analyzed_cache and analysis_task is None:
                batch_end = min(i + self.lookahead_chunks, len(chunks))
                batch = [c for c in chunks[i:batch_end] if c.text.strip() and not c.is_scene_break]
                if batch:
                    analysis_task = asyncio.create_task(
                        self._analyze_batch(batch, analyzed_cache)
                    )

            # 分析結果を待つ
            if analysis_task and i not in analyzed_cache:
                await analysis_task
                analysis_task = None

            # EXボイス: NLP分析が済んだ窓に対してクリップ選択をスケジュール
            # （analysis_task 完了後に同じ batch を再利用するため、i not in analyzed_cache が
            #   False になったタイミング = NLP完了後に起動する）
            if (
                self.ex_voice
                and ex_task is None
                and i in analyzed_cache
            ):
                batch_end = min(i + self.lookahead_chunks, len(chunks))
                batch = [c for c in chunks[i:batch_end] if c.text.strip() and not c.is_scene_break]
                if batch:
                    ex_task = asyncio.create_task(
                        self.ex_voice.analyze_window(batch, analyzed_cache)
                    )

            # NLPパイプライン結果からパラメータ生成
            params = self._get_params_for_chunk(i, chunk, analyzed_cache)

            speak_text = await self._prepare_chunk_text(chunk.text)
            if not speak_text:
                continue
            await self.tts.enqueue(speak_text, params)

            # EXボイス: このチャンク直後に挿入するクリップをキューに積む
            # pipelined（低速）プロバイダーはスキップ
            if self.ex_voice and not self.tts._is_slow_provider():
                # ex_task が完了していれば結果が _decisions に入っている
                if ex_task is not None and ex_task.done():
                    ex_task = None
                clips = self.ex_voice.pop_clips_for(chunk.index)
                for clip in clips:
                    audio = normalize_wav(clip.path.read_bytes())
                    await self.tts.enqueue_raw_audio(audio)

            # ブックマーク保存
            self.bookmarks.save(
                Bookmark(
                    source_url=chapter.source_url,
                    chapter_url=chapter_url,
                    chapter_index=chapter.chapter_index,
                    chunk_index=i,
                    title=chapter.title,
                )
            )

        if analysis_task:
            analysis_task.cancel()
        if ex_task:
            ex_task.cancel()

    async def _analyze_batch(
        self,
        chunks: list[Chunk],
        cache: dict[int, list[AnalyzedSegment]],
    ) -> None:
        """チャンクバッチをNLPパイプラインで分析."""
        for chunk in chunks:
            analyzed = await self._nlp_analyzer.analyze_chunk(
                chunk,
                known_characters=(
                    self._character_db.known_names if self._character_db else None
                ),
                character_db=self._character_db,
            )
            cache[chunk.index] = analyzed

    async def _read_chunks_file_batch(
        self,
        chunks: list[Chunk],
        source: ContentSource,
        chapter: Chapter,
        chapter_url: str,
        start_chunk: int = 0,
    ) -> None:
        """低速プロバイダー用: パイプライン合成→結合→再生.

        ローカル側でチャンクを保持し、VoiSona側のキュー深さを
        synth_concurrency 以下に抑える（流量制御）。

        Pipeline:
          [NLP準備] → local_queue → [Semaphore(N)] → VoiSona
                       (無制限)                       (同時N件まで)
        """
        speakable = [
            c for c in chunks[start_chunk:]
            if c.text.strip() and not c.is_scene_break
        ]
        if not speakable:
            return

        # 作業ディレクトリ作成
        # タイムスタンプ付きで毎回ユニークなパスにすることで
        # VoiSona内部キューの「File already exists」衝突を防ぐ
        work_id = hashlib.md5(chapter_url.encode()).hexdigest()[:8]
        ts = int(time.time())
        work_dir = self.output_dir / f"_read_{work_id}_{ts}"
        work_dir.mkdir(parents=True, exist_ok=True)

        provider = self.tts._select_provider()

        # VoiSona側の同時処理数を制限するセマフォ
        synth_sem = asyncio.Semaphore(self.synth_concurrency)

        # NLP分析キャッシュ（逐次更新）
        analyzed_cache: dict[int, list[AnalyzedSegment]] = {}

        # 完了したWAVのパスを保持（chunk.index → Path）
        completed: dict[int, Path] = {}

        # ───────────────────────────────────────────
        # NLP準備ステージ: ローカルキューに積む
        # ───────────────────────────────────────────
        PreparedChunk = tuple[Chunk, str, object, object]  # chunk, speak_text, params, kwargs
        local_queue: asyncio.Queue[PreparedChunk | None] = asyncio.Queue(
            maxsize=self.synth_concurrency * 4  # ローカルバッファ上限
        )

        async def producer() -> None:
            """NLP処理を済ませてキューに積む."""
            for i, chunk in enumerate(speakable):
                if self._stop_event.is_set():
                    break
                self._current_chunk_idx = chunk.index

                # 先読みNLP分析
                if chunk.index not in analyzed_cache:
                    next_batch = speakable[i : i + self.lookahead_chunks]
                    await self._analyze_batch(next_batch, analyzed_cache)

                params = self._get_params_for_chunk(chunk.index, chunk, analyzed_cache)
                kwargs = self.tts._build_kwargs(params)
                speak_text = await self._prepare_chunk_text(chunk.text)
                if not speak_text:
                    continue

                await local_queue.put((chunk, speak_text, params, kwargs))

            await local_queue.put(None)  # 終端マーカー

        # ───────────────────────────────────────────
        # VoiSona合成ステージ: セマフォで流量制御
        # ───────────────────────────────────────────
        async def consumer() -> None:
            """ローカルキューから取り出してVoiSonaへ送る."""
            idx = 0
            total = len(speakable)
            while True:
                item = await local_queue.get()
                if item is None:
                    break

                chunk, speak_text, params, kwargs = item
                filename = f"{chunk.index:04d}.wav"
                host_path = work_dir / filename
                vm_path = f"{self.vm_mount}\\{work_dir.name}\\{filename}"

                idx += 1
                logger.debug(
                    f"File synth [{idx}/{total}] "
                    f"(queue={local_queue.qsize()}): {speak_text[:30]}..."
                )

                async with synth_sem:  # VoiSona側の同時数を制限
                    try:
                        await provider.synthesize_to_file(
                            speak_text, vm_path, speed=params.speed, **kwargs
                        )
                        if host_path.exists():
                            completed[chunk.index] = host_path
                        else:
                            logger.warning(f"WAV not found: {host_path}")
                    except Exception as e:
                        logger.error(f"File synthesis failed: {e}")

                self.bookmarks.save(
                    Bookmark(
                        source_url=chapter.source_url,
                        chapter_url=chapter_url,
                        chapter_index=chapter.chapter_index,
                        chunk_index=chunk.index,
                        title=chapter.title,
                    )
                )

        # ───────────────────────────────────────────
        # 並行実行: NLP準備(producer) と VoiSona合成(consumer) を同時に走らせる
        # ───────────────────────────────────────────
        try:
            await asyncio.gather(producer(), consumer())
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            raise

        # チャンク順に並べてWAVリスト作成
        wav_files = [
            completed[c.index]
            for c in speakable
            if c.index in completed
        ]

        if not wav_files:
            logger.warning("No WAV files generated")
            return

        # 結合
        output_path = work_dir / "full.wav"
        logger.info(
            f"Concatenating {len(wav_files)}/{len(speakable)} WAV files..."
        )
        concat_wav_files(wav_files, output_path)

        # 再生
        if output_path.exists():
            logger.info(
                f"Playing: {output_path} ({output_path.stat().st_size} bytes)"
            )
            audio_data = output_path.read_bytes()
            await play_wav(audio_data)
        else:
            logger.error(f"Concatenated file not found: {output_path}")

        # クリーンアップ（個別チャンクファイル削除、fullは残す）
        for f in wav_files:
            f.unlink(missing_ok=True)

    async def _prepare_chunk_text(self, text: str) -> str:
        """チャンクテキストをTTS合成直前に最終クリーニング.

        1. 残存 Markdown 記法を除去（LLM出力・Pandoc残滓）
        2. 残存 LaTeX/$ 数式を変換
        3. アルファベットをチャンク単位でカタカナに変換（LLM、ASCII比率10%以上のみ）
        """
        # Markdown 記法除去（**bold**, *italic*, ~~strike~~）
        text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
        text = re.sub(r"~~([^~\n]+)~~", r"\1", text)
        # 行頭見出し（### など）
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # 箇条書きマーカー（*  ・  - ）
        text = re.sub(r"^[\s]*[-*•]\s+", "", text, flags=re.MULTILINE)
        # 番号付きリスト（1. 2. など）
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)

        # 残存 $...$ を数式変換
        text = self.processor._math.process_text(text)

        # アルファベットをチャンク単位でカタカナ変換（LLM）
        # 日本語テキストに数式変数(A,B,f,g)が混在する程度ならromanize不要
        # → ASCII比率が10%以上ある場合のみ変換（それ未満は変数名だけで日本語TTS問題なし）
        if (
            self.scene_analyzer
            and TextProcessor.has_alphabet(text)
            and len(text) < 400
            and _ascii_ratio(text) >= 0.10
        ):
            text = await self.scene_analyzer._backend.romanize(text)

        # VoiSona 向け最終正規化
        text = _normalize_for_tts(text)

        return text.strip()

    def _get_params_for_chunk(
        self,
        chunk_idx: int,
        chunk: Chunk,
        cache: dict[int, list[AnalyzedSegment]],
    ) -> TTSParams:
        """チャンクのNLP分析結果からTTSパラメータを生成."""
        analyzed_segments = cache.get(chunk_idx)
        if not analyzed_segments:
            return TTSParams()

        # チャンク内の最も支配的なセグメントのパラメータを使用
        # （複数セグメントがある場合、最も長いもの）
        dominant = max(analyzed_segments, key=lambda s: len(s.text)) if analyzed_segments else None
        if not dominant:
            return TTSParams()

        return self.param_mapper.map(dominant, self._character_db)


def _ascii_ratio(text: str) -> float:
    """テキスト中のASCII文字（スペース・改行除く）の比率を返す."""
    stripped = re.sub(r"[\s\n]", "", text)
    if not stripped:
        return 0.0
    ascii_count = sum(1 for c in stripped if c.isascii() and c.isalpha())
    return ascii_count / len(stripped)


def _normalize_for_tts(text: str) -> str:
    """VoiSona送信直前の最終正規化.

    改行で分断された単一トークンの連結、重複除去、
    全角記号の半角化など TTS エンジンをクラッシュさせやすい
    パターンを除去する。
    """
    # 全角算術記号・括弧を半角に変換
    _FULLWIDTH = str.maketrans(
        "＝＋－×÷（）［］｛｝＜＞",
        "=+-×÷()[]{}＜＞",
    )
    text = text.translate(_FULLWIDTH)

    # 改行を空白に統一
    text = text.replace("\n", " ")
    # 連続スペースを1つに
    text = re.sub(r" {2,}", " ", text)
    # 空白で分割して隣接する重複トークンを除去
    # 例: "A A から B B へ" → "A から B へ"
    tokens = text.split(" ")
    deduped: list[str] = []
    for tok in tokens:
        if tok and (not deduped or tok != deduped[-1]):
            deduped.append(tok)
    return " ".join(deduped)
