"""Reading engine — main orchestrator for text-to-speech reading."""

import asyncio
import hashlib
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from ..nlp.classifier import TextClassifier
from ..nlp.scene_analyzer import AnalyzedSegment, SceneAnalyzer
from ..nlp.speaker import SpeakerExtractor
from ..nlp.splitter import Chunk, TextSplitter
from ..nlp.text_processor import TextProcessor
from ..sources import registry
from ..sources.base import Chapter, ContentSource
from ..tts.base import TTSParams
from ..tts.manager import TTSManager
from ..tts.playback import play_wav
from .bookmark import Bookmark, BookmarkManager
from .character_db import CharacterDB
from .param_mapper import ParamMapper


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

        self._running = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_event = asyncio.Event()
        self._current_source: ContentSource | None = None
        self._current_chapter: Chapter | None = None
        self._current_chunks: list[Chunk] = []
        self._current_chunk_idx: int = 0
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
        }

    async def read_url(self, url: str, start_chunk: int = 0) -> None:
        """URLから読み上げを開始."""
        self._stop_event.clear()
        self._running = True

        # 作品IDを生成（URLハッシュ）
        work_id = hashlib.md5(url.encode()).hexdigest()[:12]
        self._character_db = CharacterDB(work_id)

        try:
            source = registry.resolve(url)
            self._current_source = source
            await self._read_from_source(source, url, start_chunk)
        except Exception as e:
            logger.error(f"Reading failed: {e}")
            raise
        finally:
            self._running = False

    async def resume_last(self) -> None:
        """最後のブックマークから再開."""
        bookmark = self.bookmarks.get_last()
        if not bookmark:
            logger.warning("No bookmark found to resume from")
            return

        logger.info(
            f"Resuming: {bookmark.work_title or bookmark.source_url} "
            f"ch={bookmark.chapter_index} chunk={bookmark.chunk_index}"
        )
        await self.read_url(bookmark.chapter_url, start_chunk=bookmark.chunk_index)

    def pause(self) -> None:
        self._pause_event.clear()
        self.tts.pause()
        logger.info("Reading paused")

    def resume(self) -> None:
        self._pause_event.set()
        self.tts.resume()
        logger.info("Reading resumed")

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        logger.info("Reading stopped")

    async def _read_from_source(
        self, source: ContentSource, url: str, start_chunk: int = 0
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

            # アルファベット→読み仮名変換（LLM）
            if self.scene_analyzer and TextProcessor.has_alphabet(clean_text):
                clean_text = await self.scene_analyzer._backend.romanize(clean_text)

            # チャンク分割
            chunks = self.splitter.split(clean_text)
            self._current_chunks = chunks

            # 低速プロバイダーはファイル出力→結合→再生
            if self.tts._is_slow_provider():
                await self._read_chunks_file_batch(
                    chunks, source, chapter, chapter_url, start_chunk
                )
            else:
                # TTS合成・再生ループ
                await self.tts.start()
                try:
                    await self._read_chunks(
                        chunks, source, chapter, chapter_url, start_chunk
                    )
                finally:
                    if self._stop_event.is_set():
                        await self.tts.stop()
                    else:
                        await self.tts.drain()

            if self._stop_event.is_set():
                break

            # 次章への自動遷移
            start_chunk = 0
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

            # NLPパイプライン結果からパラメータ生成
            params = self._get_params_for_chunk(i, chunk, analyzed_cache)

            await self.tts.enqueue(chunk.text, params)

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

    async def _analyze_batch(
        self,
        chunks: list[Chunk],
        cache: dict[int, list[AnalyzedSegment]],
    ) -> None:
        """チャンクバッチをNLPパイプラインで分析."""
        for chunk in chunks:
            # Stage 1: ルールベース分類
            segments = self.classifier.classify(chunk.text)
            # Stage 1.5: 話者候補抽出
            segments = self.speaker_extractor.extract(segments)

            # Stage 2: SLM分析（利用可能な場合）
            if self.scene_analyzer:
                analyzed = await self.scene_analyzer.analyze_batch(
                    segments,
                    known_characters=self._character_db.known_names if self._character_db else None,
                )
                # 新キャラクター検出
                for seg in analyzed:
                    if seg.new_character and self._character_db:
                        self._character_db.get_or_create(
                            seg.new_character.get("name", ""),
                            profile_hint=seg.new_character,
                        )
                    if seg.speaker and self._character_db:
                        self._character_db.get_or_create(seg.speaker)
            else:
                analyzed = [
                    AnalyzedSegment.from_segment(
                        seg,
                        speaker=seg.speaker_candidates[0] if seg.speaker_candidates else None,
                    )
                    for seg in segments
                ]

            cache[chunk.index] = analyzed

    async def _read_chunks_file_batch(
        self,
        chunks: list[Chunk],
        source: ContentSource,
        chapter: Chapter,
        chapter_url: str,
        start_chunk: int = 0,
    ) -> None:
        """低速プロバイダー用: チャンクごとにファイル合成→結合→再生."""
        speakable = [
            c for c in chunks[start_chunk:]
            if c.text.strip() and not c.is_scene_break
        ]
        if not speakable:
            return

        # 作業ディレクトリ作成
        work_id = hashlib.md5(chapter_url.encode()).hexdigest()[:12]
        work_dir = self.output_dir / f"_read_{work_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        provider = self.tts._select_provider()
        wav_files: list[Path] = []

        # NLP分析
        analyzed_cache: dict[int, list[AnalyzedSegment]] = {}
        batch = speakable[: self.lookahead_chunks]
        if batch:
            await self._analyze_batch(batch, analyzed_cache)

        try:
            for i, chunk in enumerate(speakable):
                if self._stop_event.is_set():
                    break

                self._current_chunk_idx = chunk.index

                # 先読み分析
                if chunk.index not in analyzed_cache:
                    next_batch = speakable[i : i + self.lookahead_chunks]
                    await self._analyze_batch(next_batch, analyzed_cache)

                params = self._get_params_for_chunk(chunk.index, chunk, analyzed_cache)
                kwargs = self.tts._build_kwargs(params)

                filename = f"{chunk.index:04d}.wav"
                host_path = work_dir / filename
                vm_path = f"{self.vm_mount}\\{work_dir.name}\\{filename}"

                logger.debug(
                    f"File synth [{i + 1}/{len(speakable)}]: "
                    f"{chunk.text[:30]}..."
                )
                try:
                    await provider.synthesize_to_file(
                        chunk.text, vm_path, speed=params.speed, **kwargs
                    )
                    if host_path.exists():
                        wav_files.append(host_path)
                    else:
                        logger.warning(f"WAV not found: {host_path}")
                except Exception as e:
                    logger.error(f"File synthesis failed: {e}")

                # ブックマーク保存
                self.bookmarks.save(
                    Bookmark(
                        source_url=chapter.source_url,
                        chapter_url=chapter_url,
                        chapter_index=chapter.chapter_index,
                        chunk_index=chunk.index,
                        title=chapter.title,
                    )
                )

            if not wav_files:
                logger.warning("No WAV files generated")
                return

            # 結合
            output_path = work_dir / "full.wav"
            logger.info(f"Concatenating {len(wav_files)} WAV files...")
            self._concat_wav_files(wav_files, output_path)

            # 再生
            if output_path.exists():
                logger.info(f"Playing: {output_path} ({output_path.stat().st_size} bytes)")
                audio_data = output_path.read_bytes()
                await play_wav(audio_data)
            else:
                logger.error(f"Concatenated file not found: {output_path}")

        finally:
            # クリーンアップ（個別チャンクファイル削除、fullは残す）
            for f in wav_files:
                f.unlink(missing_ok=True)

    @staticmethod
    def _concat_wav_files(files: list[Path], output: Path) -> None:
        """ffmpeg concat demuxer でWAVファイルを結合."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            for wav in files:
                f.write(f"file '{wav.resolve()}'\n")
            concat_list = f.name

        try:
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_list, "-codec:a", "pcm_s16le", str(output),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {result.stderr}")
        finally:
            Path(concat_list).unlink(missing_ok=True)

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
