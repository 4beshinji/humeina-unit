"""統一 NLP パイプライン.

TextProcessor → TextSplitter → TextClassifier → SpeakerExtractor → SceneAnalyzer
の流れを単一クラスに集約し、reader / api / batch から再利用できるようにする.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..adapters.persistence.pronunciation import PronunciationDictionary
from ..core.text import TextDocument
from ..llm.budgeting import TokenBudget
from ..settings import AnalysisSettings
from .classifier import SegmentType, TextClassifier
from .pronunciation import propose_readings
from .scene_analyzer import AnalyzedSegment, SceneAnalyzer
from .speaker import SpeakerExtractor
from .splitter import Chunk, TextSplitter
from .text_processor import TextProcessor

if TYPE_CHECKING:
    from ..llm import LLMBackend
    from ..reader.character_db import CharacterDB


@dataclass
class ChunkAnalysis:
    """1チャンク分の分析結果."""

    chunk: Chunk
    segments: list[AnalyzedSegment]


class PlanAnalysis(list[AnalyzedSegment]):
    """同時実行でも結果と入力文書の対応を失わない解析結果。"""

    def __init__(self, segments, document: TextDocument):
        super().__init__(segments)
        self.document = document


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
        analysis_settings: AnalysisSettings | None = None,
    ):
        self.settings = analysis_settings or AnalysisSettings(
            llm_policy="full" if (llm is not None or scene_analyzer is not None) else "off"
        )
        self._dictionary = PronunciationDictionary(
            self.settings.common_dictionary, self.settings.work_dictionary
        )
        self._processor = text_processor or TextProcessor(math_dict_path=math_dict_path)
        self._splitter = splitter or TextSplitter(max_chars=max_chunk_chars)
        self._classifier = classifier or TextClassifier(self.settings.content_kind)
        self._speaker_extractor = speaker_extractor or SpeakerExtractor()
        self._scene_analyzer = None
        if self.settings.llm_policy != "off":
            self._scene_analyzer = scene_analyzer
            if scene_analyzer is not None and analysis_settings is not None:
                scene_analyzer.budget = TokenBudget(
                    self.settings.context_tokens,
                    self.settings.context_safety_ratio,
                    self.settings.max_items,
                )
                scene_analyzer.max_output_tokens = self.settings.max_output_tokens
                scene_analyzer.max_requests = self.settings.max_requests
            if scene_analyzer is None and llm is not None:
                self._scene_analyzer = SceneAnalyzer(
                    llm,
                    budget=TokenBudget(
                        self.settings.context_tokens,
                        self.settings.context_safety_ratio,
                        self.settings.max_items,
                    ),
                    max_output_tokens=self.settings.max_output_tokens,
                    max_requests=self.settings.max_requests,
                )

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
                speaker=seg.metadata.get("speaker"),
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
        return await self.analyze_for_plan(
            chunk.text,
            known_characters=known_characters,
            character_db=character_db,
        )

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
        analyzed = await self.analyze_for_plan(
            text,
            known_characters=known_characters,
            character_db=character_db,
        )
        results: list[ChunkAnalysis] = []
        for seg in analyzed:
            if seg.type == SegmentType.SCENE_BREAK:
                continue
            # 分類と話者は全文構造から確定済み。後段分割で再分類しない。
            for chunk in self._splitter.split(seg.text):
                chunk.index = len(results)
                results.append(ChunkAnalysis(chunk, [replace(seg, text=chunk.text)]))
        return results

    async def analyze_for_plan(
        self,
        text: str,
        *,
        known_characters: list[str] | None = None,
        character_db: CharacterDB | None = None,
    ) -> list[AnalyzedSegment]:
        """原文・辞書・構造を先に確定し、方針に応じて必要な対象だけを窓分析する。

        TTS文字数分割はこの後で行う。本文照合や再分類で話者境界を復元しない。
        """

        if not isinstance(text, (str, TextDocument)):
            raise TypeError("text must be a string or TextDocument")
        document = self._dictionary.apply(self._processor.process_document(text))
        if self._scene_analyzer is not None:
            document = await propose_readings(
                document, self._scene_analyzer._backend, self.settings
            )
        if not document.text:
            return PlanAnalysis([], document)
        segments = self._classifier.classify(document.text)
        segments = self._speaker_extractor.extract(segments, known_characters)
        for seg in segments:
            if seg.type == SegmentType.SCENE_BREAK:
                continue
            a, b = seg.metadata["normalized_start"], seg.metadata["normalized_end"]
            seg.metadata.update(
                text_content=document.slice(a, b),
                source_ref={
                    "start": document.source_map[a][0],
                    "end": document.source_map[b - 1][1],
                },
            )
        analyzed = [
            AnalyzedSegment.from_segment(seg, speaker=seg.metadata.get("speaker"))
            for seg in segments
        ]
        if self._scene_analyzer is not None:
            selected = [
                seg
                for seg in segments
                if seg.type != SegmentType.SCENE_BREAK
                and (
                    self.settings.llm_policy == "full"
                    or (
                        seg.type in {SegmentType.DIALOGUE, SegmentType.THOUGHT}
                        and not seg.metadata.get("speaker")
                    )
                )
            ]
            if selected:
                if self.settings.llm_policy == "selective":
                    for seg in selected:
                        before = segments[seg.index - 1].text[-200:] if seg.index else ""
                        after = (
                            segments[seg.index + 1].text[:200]
                            if seg.index + 1 < len(segments)
                            else ""
                        )
                        seg.metadata["analysis_context"] = before + "\n" + after
                updates = await self._scene_analyzer.analyze_batch(
                    selected,
                    known_characters=known_characters,
                )
                by_id = {seg.index: seg for seg in updates}
                analyzed = [by_id.get(seg.index, seg) for seg in analyzed]
        if character_db is not None:
            self._update_character_db(character_db, analyzed)
        return PlanAnalysis(analyzed, document)

    # Descriptive aliases keep the migration boundary discoverable to callers that do not know
    # whether the input is a Reader chunk stream or a plan-level analysis.
    analyze_unsplit = analyze_for_plan
    analyze_plan = analyze_for_plan

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
