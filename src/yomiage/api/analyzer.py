"""TextAnalyzer — NLP analysis facade for the public API."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..nlp.llm_backend import LLMBackend, create_llm_backend
from ..nlp.pipeline import NLPAnalyzer
from ..nlp.scene_analyzer import AnalyzedSegment, SceneAnalyzer
from .models import AnalysisResult

if TYPE_CHECKING:
    from .config import AnalyzerConfig


class TextAnalyzer:
    """テキスト分析ファサード: 分類→話者識別→感情/シーン分析.

    Usage::

        analyzer = TextAnalyzer.create("ollama", url="http://localhost:11434")
        segments = await analyzer.analyze("太郎は「おはよう」と言った。")
    """

    def __init__(
        self,
        llm: LLMBackend | None = None,
        max_chunk_chars: int = 200,
    ):
        self._analyzer = NLPAnalyzer(
            llm=llm,
            max_chunk_chars=max_chunk_chars,
        )

    @property
    def _scene_analyzer(self) -> SceneAnalyzer | None:
        """互換性: 内部 SceneAnalyzer へのアクセス."""
        return self._analyzer.scene_analyzer

    @classmethod
    def create(cls, llm_backend: str = "ollama", **kwargs: object) -> TextAnalyzer:
        """ファクトリ: バックエンド名+パラメータで作成.

        Args:
            llm_backend: "ollama", "openai", or "anthropic"
            **kwargs: url, api_key, model etc.
        """
        backend = create_llm_backend(llm_backend, **kwargs)
        return cls(llm=backend)

    @classmethod
    def from_config(cls, config: AnalyzerConfig) -> TextAnalyzer:
        """AnalyzerConfigから作成."""
        llm = create_llm_backend(
            config.llm.backend,
            url=config.llm.url,
            api_key=config.llm.api_key,
            model=config.llm.model,
        )
        return cls(llm=llm, max_chunk_chars=config.max_chunk_chars)

    @classmethod
    def rule_based(cls, max_chunk_chars: int = 200) -> TextAnalyzer:
        """LLMなしのルールベースのみで作成."""
        return cls(llm=None, max_chunk_chars=max_chunk_chars)

    def preprocess(self, text: str) -> str:
        """テキスト前処理（正規化、HTMLクリーンアップ）."""
        return self._analyzer.preprocess(text)

    def classify(self, text: str) -> list[AnalysisResult]:
        """ルールベース分類のみ（同期、LLM不要）."""
        return [
            _to_analysis_result(seg)
            for seg in self._analyzer.classify(text)
        ]

    async def analyze(
        self,
        text: str,
        *,
        known_characters: list[str] | None = None,
    ) -> list[AnalysisResult]:
        """完全分析: 分類 + 話者識別 + SLM感情/シーン分析."""
        clean = self._analyzer.preprocess(text)
        chunks = self._analyzer.split(clean)

        results: list[AnalysisResult] = []
        for chunk in chunks:
            if chunk.is_scene_break:
                results.append(AnalysisResult(text="", segment_type="scene_break"))
                continue
            if not chunk.text.strip():
                continue

            analyzed = await self._analyzer.analyze_chunk(
                chunk,
                known_characters=known_characters,
            )
            for seg in analyzed:
                results.append(_to_analysis_result(seg))
        return results

    # --- sync ラッパー ---

    def analyze_sync(
        self, text: str, **kwargs: object
    ) -> list[AnalysisResult]:
        """analyze の同期版."""
        return asyncio.run(self.analyze(text, **kwargs))


def _to_analysis_result(seg: AnalyzedSegment) -> AnalysisResult:
    """内部AnalyzedSegmentを公開AnalysisResultに変換."""
    return AnalysisResult(
        text=seg.text,
        segment_type=seg.type.value,
        speaker=seg.speaker,
        scene=seg.scene,
        emotion=seg.emotion,
        intensity=seg.intensity,
    )
