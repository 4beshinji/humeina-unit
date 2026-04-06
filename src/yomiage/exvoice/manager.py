"""EXボイスマネージャー — カタログ・セレクター・スパム防止を統合."""

from __future__ import annotations

from loguru import logger

from ..nlp.scene_analyzer import AnalyzedSegment
from ..nlp.splitter import Chunk
from .catalog import VoiceClip, find_text_matches
from .selector import ClipInsertion, ExVoiceSelector


class ExVoiceManager:
    """EXボイスクリップ挿入の全体制御.

    ReadingEngine._read_chunks() のルックアヘッドループと連携し、
    チャンク窓ごとに挿入決定（_decisions）を事前に計算しておく。
    再生時は pop_clips_for() で当該チャンク分のクリップを消費する。
    """

    def __init__(
        self,
        catalog: list[VoiceClip],
        selector: ExVoiceSelector,
        cooldown_chunks: int = 10,
        max_per_chapter: int = 8,
        llm_max_insertions: int = 2,
    ):
        self._catalog = catalog
        self._selector = selector
        self._cooldown_chunks = cooldown_chunks
        self._max_per_chapter = max_per_chapter
        self._llm_max_insertions = llm_max_insertions

        # 章ごとにリセットされる状態
        self._decisions: dict[int, VoiceClip] = {}   # chunk.index → clip
        self._last_chunk: int = -(cooldown_chunks + 1)
        self._chapter_count: int = 0

    def reset_chapter(self) -> None:
        self._decisions.clear()
        self._last_chunk = -(self._cooldown_chunks + 1)
        self._chapter_count = 0

    async def analyze_window(
        self,
        chunks: list[Chunk],
        analyzed_cache: dict[int, list[AnalyzedSegment]],
    ) -> None:
        """チャンク窓を分析して挿入決定を _decisions に格納.

        テキスト一致（同期）→ LLM判断（非同期）の順に実行。
        両方の結果を _decisions にマージするが、1チャンク1クリップを維持。
        """
        # テキスト一致（高速・確実性重視）
        matches = find_text_matches(chunks, self._catalog)
        for chunk_idx, clip in matches.items():
            if chunk_idx not in self._decisions:
                self._decisions[chunk_idx] = clip
                logger.debug(
                    f"ExVoice text_match: chunk={chunk_idx} clip='{clip.text}'"
                )

        # LLM判断（文脈理解重視）
        insertions: list[ClipInsertion] = await self._selector.select(
            chunks,
            analyzed_cache,
            max_insertions=self._llm_max_insertions,
        )
        for ins in insertions:
            if ins.after_chunk_index not in self._decisions:
                self._decisions[ins.after_chunk_index] = ins.clip
                logger.debug(
                    f"ExVoice llm: chunk={ins.after_chunk_index} "
                    f"clip='{ins.clip.text}'"
                )

    def pop_clips_for(self, chunk_index: int) -> list[VoiceClip]:
        """チャンク再生後に挿入するクリップを返して消費する.

        クールダウン・章上限を適用。挿入しない場合は空リストを返す。
        """
        clip = self._decisions.pop(chunk_index, None)
        if clip is None:
            return []
        if self._chapter_count >= self._max_per_chapter:
            logger.debug(f"ExVoice: chapter cap ({self._max_per_chapter}) reached, skipping")
            return []
        gap = chunk_index - self._last_chunk
        if gap < self._cooldown_chunks:
            logger.debug(
                f"ExVoice: cooldown ({gap}/{self._cooldown_chunks} chunks), skipping"
            )
            return []

        self._last_chunk = chunk_index
        self._chapter_count += 1
        logger.info(
            f"EX voice: inserting '{clip.text}' after chunk {chunk_index} "
            f"({self._chapter_count}/{self._max_per_chapter} this chapter)"
        )
        return [clip]
