"""Reading engine — main orchestrator for text-to-speech reading."""

import asyncio
import hashlib
import re
import time
from pathlib import Path

from loguru import logger

from ..exvoice.catalog import normalize_wav
from ..exvoice.manager import ExVoiceManager
from ..nlp.classifier import TextClassifier
from ..nlp.pipeline import NLPAnalyzer
from ..nlp.scene_analyzer import AnalyzedSegment, SceneAnalyzer
from ..nlp.speaker import SpeakerExtractor
from ..nlp.splitter import Chunk, TextSplitter
from ..nlp.text_processor import TextProcessor
from ..sources import registry
from ..sources.base import Chapter, ContentSource
from ..tts.audio_utils import concat_wav_files
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
        synth_concurrency: int = 2,
        ex_voice: ExVoiceManager | None = None,
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

            # チャンク分割（romanize はチャンク単位で行う）
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
