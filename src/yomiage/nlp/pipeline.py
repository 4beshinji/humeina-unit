"""統一 NLP パイプライン.

TextProcessor → TextSplitter → TextClassifier → SpeakerExtractor → SceneAnalyzer
の流れを単一クラスに集約し、reader / api / batch から再利用できるようにする.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .classifier import TextClassifier
from .scene_analyzer import AnalyzedSegment, SceneAnalyzer
from .speaker import SpeakerExtractor
from .splitter import Chunk, TextSplitter
from .text_processor import TextProcessor

if TYPE_CHECKING:
    from ..reader.character_db import CharacterDB
    from .llm_backend import LLMBackend


@dataclass
class ChunkAnalysis:
    """1チャンク分の分析結果."""

    chunk: Chunk
    segments: list[AnalyzedSegment]


class NLPAnalyzer:
    """テキスト前処理から話者・シーン・感情分析までの統合パイプライン.

    Usage::

        analyzer = NLPAnalyzer(llm=backend, max_chunk_chars=200)
        results = await analyzer.analyze_text(text, character_db=db)
    """

    def __init__(
        self,
        llm: LLMBackend | None = None,
        max_chunk_chars: int = 200,
        text_processor: TextProcessor | None = None,
        splitter: TextSplitter | None = None,
        classifier: TextClassifier | None = None,
        speaker_extractor: SpeakerExtractor | None = None,
        scene_analyzer: SceneAnalyzer | None = None,
        math_dict_path: Path | None = None,
    ):
        self._processor = text_processor or TextProcessor(
            math_dict_path=math_dict_path
        )
        self._splitter = splitter or TextSplitter(max_chars=max_chunk_chars)
        self._classifier = classifier or TextClassifier()
        self._speaker_extractor = speaker_extractor or SpeakerExtractor()
        self._scene_analyzer = scene_analyzer
        if scene_analyzer is None and llm is not None:
            self._scene_analyzer = SceneAnalyzer(llm)

    @property
    def scene_analyzer(self) -> SceneAnalyzer | None:
        """使用している SceneAnalyzer を返す."""
        return self._scene_analyzer

    @classmethod
    def rule_based(
        cls,
        max_chunk_chars: int = 200,
        text_processor: TextProcessor | None = None,
        splitter: TextSplitter | None = None,
        classifier: TextClassifier | None = None,
        speaker_extractor: SpeakerExtractor | None = None,
    ) -> "NLPAnalyzer":
        """LLMなしのルールベースパイプラインを作成."""
        return cls(
            llm=None,
            max_chunk_chars=max_chunk_chars,
            text_processor=text_processor,
            splitter=splitter,
            classifier=classifier,
            speaker_extractor=speaker_extractor,
        )

    def preprocess(self, text: str) -> str:
        """テキスト前処理（正規化、HTMLクリーンアップ）."""
        return self._processor.process(text)

    def split(self, text: str) -> list[Chunk]:
        """テキストをチャンクに分割."""
        return self._splitter.split(self._processor.process(text))

    def classify(self, text: str) -> list[AnalyzedSegment]:
        """ルールベース分類 + 話者抽出のみ（LLM不要）."""
        segments = self._classifier.classify(text)
        segments = self._speaker_extractor.extract(segments)
        return [
            AnalyzedSegment.from_segment(
                seg,
                speaker=seg.speaker_candidates[0] if seg.speaker_candidates else None,
            )
            for seg in segments
        ]

    async def analyze_chunk(
        self,
        chunk: Chunk,
        *,
        known_characters: list[str] | None = None,
        character_db: CharacterDB | None = None,
    ) -> list[AnalyzedSegment]:
        """単一チャンクを完全分析.

        Args:
            chunk: 分析対象チャンク
            known_characters: 既知キャラクター名リスト
            character_db: 新キャラ検出時に更新する CharacterDB
        """
        segments = self._classifier.classify(chunk.text)
        segments = self._speaker_extractor.extract(segments)

        if self._scene_analyzer:
            analyzed = await self._scene_analyzer.analyze_batch(
                segments,
                known_characters=known_characters,
            )
            if character_db is not None:
                self._update_character_db(character_db, analyzed)
        else:
            analyzed = [
                AnalyzedSegment.from_segment(
                    seg,
                    speaker=seg.speaker_candidates[0] if seg.speaker_candidates else None,
                )
                for seg in segments
            ]

        return analyzed

    async def analyze_text(
        self,
        text: str,
        *,
        known_characters: list[str] | None = None,
        character_db: CharacterDB | None = None,
    ) -> list[ChunkAnalysis]:
        """テキスト全体を前処理・分割・分析.

        Returns:
            チャンクごとの分析結果リスト
        """
        clean = self._processor.process(text)
        if not clean:
            return []

        chunks = self._splitter.split(clean)
        results: list[ChunkAnalysis] = []
        for chunk in chunks:
            if chunk.is_scene_break or not chunk.text.strip():
                continue
            analyzed = await self.analyze_chunk(
                chunk,
                known_characters=known_characters,
                character_db=character_db,
            )
            results.append(ChunkAnalysis(chunk=chunk, segments=analyzed))
        return results

    @staticmethod
    def _update_character_db(
        character_db: CharacterDB,
        analyzed: list[AnalyzedSegment],
    ) -> None:
        """分析結果から新キャラクターを CharacterDB に反映."""
        for seg in analyzed:
            if seg.new_character:
                character_db.get_or_create(
                    seg.new_character.get("name", ""),
                    profile_hint=seg.new_character,
                )
            if seg.speaker:
                character_db.get_or_create(seg.speaker)
