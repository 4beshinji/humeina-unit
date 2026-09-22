"""Reading engine — main orchestrator for text-to-speech reading."""

import asyncio
import hashlib
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from ..application.planning import ReaderPlanningAdapter, UtterancePlanner
from ..application.synthesis import SynthesisService
from ..application.voice_parameters import VoiceResolutionContext
from ..core.models import SourceRef, Utterance, VoiceIntent
from ..core.plan import UtterancePlan
from ..core.text import TextDocument
from ..exvoice.catalog import normalize_wav
from ..exvoice.manager import ExVoiceManager
from ..nlp.classifier import TextClassifier
from ..nlp.pipeline import NLPAnalyzer
from ..nlp.scene_analyzer import AnalyzedSegment, SceneAnalyzer
from ..nlp.speaker import SpeakerExtractor
from ..nlp.splitter import Chunk, TextSplitter
from ..nlp.text_processor import TextProcessor
from ..ports.sources import URLPolicy
from ..runtime_paths import data_dir as default_data_dir
from ..runtime_paths import output_dir as default_output_dir
from ..settings import AnalysisSettings
from ..sources import registry
from ..sources.base import Chapter, ContentSource
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
        output_dir: str | None = None,
        synth_concurrency: int = 2,
        ex_voice: ExVoiceManager | None = None,
        synthesis_service: SynthesisService | None = None,
        planning_adapter: ReaderPlanningAdapter | None = None,
        playback_queue: UtterancePlaybackQueue | None = None,
        url_policy: URLPolicy | None = None,
        metrics: Any | None = None,
        data_dir: Path | str | None = None,
        analysis_settings: AnalysisSettings | None = None,
    ):
        self.analysis_settings = analysis_settings
        self.tts = tts_manager
        self.processor = text_processor or TextProcessor()
        self.splitter = splitter or TextSplitter()
        self.classifier = classifier or TextClassifier(
            analysis_settings.content_kind if analysis_settings else "novel"
        )
        self.speaker_extractor = speaker_extractor or SpeakerExtractor()
        self.scene_analyzer = scene_analyzer
        self.llm = scene_analyzer._backend if scene_analyzer is not None else None
        self._owns_llm = False
        self.param_mapper = param_mapper or ParamMapper.from_config_file()
        self.data_dir = Path(data_dir or default_data_dir()).expanduser().resolve()
        self.bookmarks = bookmark_manager or BookmarkManager(self.data_dir / "bookmarks")
        self.auto_advance = auto_advance
        self.lookahead_chunks = lookahead_chunks
        self.vm_mount = vm_mount
        self.output_dir = Path(output_dir or default_output_dir()).expanduser().resolve()
        self.synth_concurrency = synth_concurrency
        self.ex_voice = ex_voice
        self.synthesis_service = synthesis_service
        if self.synthesis_service is not None and metrics is not None:
            # Injected services remain caller-owned, but may still share the
            # process metrics port with this composition root.
            if getattr(self.synthesis_service, "metrics", None) is None:
                try:
                    self.synthesis_service.metrics = metrics
                except AttributeError:  # pragma: no cover - immutable fake
                    pass
        self._owns_synthesis_service = False
        self._planning_adapter = planning_adapter
        self._playback_queue = playback_queue
        self._owns_playback_queue = False
        self.url_policy = url_policy
        self.metrics = metrics

        self._nlp_analyzer = NLPAnalyzer(
            max_chunk_chars=self.splitter.max_chars,
            text_processor=self.processor,
            splitter=self.splitter,
            classifier=self.classifier,
            speaker_extractor=self.speaker_extractor,
            scene_analyzer=self.scene_analyzer,
            analysis_settings=analysis_settings,
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
        self._character_db = CharacterDB(work_id, data_dir=self.data_dir / "characters")

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

    async def close(self) -> None:
        """Readerが所有するTTS/LLM資源を閉じる。"""

        await self.tts.close()
        if self._owns_llm and self.llm is not None:
            await self.llm.close()
            self._owns_llm = False

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
            clean_text = chapter.document or TextDocument.plain(chapter.text)
            if not clean_text.text.strip():
                logger.warning(f"Empty chapter: {chapter.title}")
                break

            # 原文と読み指定を保ったPlanを作り、TTS制限による分割は合成へ委ねる。
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
        """service注入がない旧CLI向けにcanonical serviceを組み立てる。"""

        if self.synthesis_service is not None:
            return self.synthesis_service

        from ..bootstrap import create_synthesis_service

        providers = {self.tts.primary.name: self.tts.primary}
        if self.tts.fallback is not None and self.tts.fallback.name != self.tts.primary.name:
            providers[self.tts.fallback.name] = self.tts.fallback
        self.synthesis_service = create_synthesis_service(
            providers,
            primary=self.tts.primary.name,
            fallback=self.tts.fallback.name if self.tts.fallback is not None else None,
            concurrency=max(1, self.synth_concurrency),
            metrics=self.metrics,
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
            intent = utterance.voice_intent
            # voice_id自体はcoreへ持ち込まず、preferred profileとしてadapterに解決させる。
            preferred = intent.preferred_voice_profile or profile.voice_id
            updated = VoiceIntent(
                archetype=intent.archetype or profile.personality,
                gender=intent.gender or profile.gender,
                age_group=intent.age_group or profile.age_group,
                speaking_rate=intent.speaking_rate,
                pitch=intent.pitch,
                volume=intent.volume,
                intonation=intent.intonation,
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
        clean_text = text
        if not clean_text.strip():
            return []
        self._stop_event.clear()
        self._running = True
        work_id = hashlib.md5((source_url or clean_text).encode()).hexdigest()[:12]
        self._character_db = CharacterDB(work_id, data_dir=self.data_dir / "characters")
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

        if self.ex_voice:
            self.ex_voice.reset_chapter()
            remaining = [u for u in plan.utterances if u.order >= start_order]
            for start in range(0, len(remaining), max(1, self.lookahead_chunks)):
                window = remaining[start:start + max(1, self.lookahead_chunks)]
                chunks = [Chunk(u.text, u.order) for u in window]
                analyzed = {u.order: [AnalyzedSegment(
                    u.text, u.segment_type, u.order, speaker=u.speaker_id,
                    scene=u.scene, emotion=u.emotion.value, intensity=u.intensity,
                )] for u in window}
                await self.ex_voice.analyze_window(chunks, analyzed)

        async def on_played(item: Utterance, _result: Any) -> None:
            if self.ex_voice:
                for clip in self.ex_voice.pop_clips_for(item.order):
                    if self._stop_event.is_set():
                        break
                    await play_wav(normalize_wav(clip.path.read_bytes()))
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

        def parameter_context_factory(item: Utterance) -> VoiceResolutionContext:
            return self.param_mapper.context_for(
                item, self._character_db, voice_profile=getattr(self.tts, "voice_profile", None),
                provider=self.tts.primary.name,
                pitch_scale=getattr(self.tts.primary, "pitch_scale", 300.0),
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
                parameter_context_factory=parameter_context_factory,
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
