"""Tests for TextAnalyzer."""

from unittest.mock import AsyncMock, patch

import pytest

from yomiage.api.analyzer import TextAnalyzer, _to_analysis_result
from yomiage.api.config import AnalyzerConfig, LLMConfig
from yomiage.api.models import AnalysisResult
from yomiage.nlp.classifier import SegmentType
from yomiage.nlp.scene_analyzer import AnalyzedSegment


class TestTextAnalyzerInit:
    def test_rule_based_only(self):
        analyzer = TextAnalyzer.rule_based()
        assert analyzer._scene_analyzer is None

    def test_from_config(self):
        config = AnalyzerConfig(
            llm=LLMConfig(backend="ollama", url="http://localhost:11434"),
            max_chunk_chars=150,
        )
        analyzer = TextAnalyzer.from_config(config)
        assert analyzer._scene_analyzer is not None


class TestTextAnalyzerPreprocess:
    def test_basic_preprocess(self):
        analyzer = TextAnalyzer.rule_based()
        result = analyzer.preprocess("  テスト　テキスト  ")
        assert "テスト" in result


class TestTextAnalyzerClassify:
    def test_classify_dialogue(self):
        analyzer = TextAnalyzer.rule_based()
        results = analyzer.classify("太郎は「おはようございます」と言った。")
        assert isinstance(results, list)
        assert len(results) >= 1
        types = {r.segment_type for r in results}
        assert "dialogue" in types

    def test_classify_narration(self):
        analyzer = TextAnalyzer.rule_based()
        results = analyzer.classify("空は青く澄んでいた。")
        assert len(results) == 1
        assert results[0].segment_type == "narration"

    def test_classify_thought(self):
        analyzer = TextAnalyzer.rule_based()
        results = analyzer.classify("（これはまずい）と思った。")
        types = {r.segment_type for r in results}
        assert "thought" in types

    def test_classify_mixed(self):
        analyzer = TextAnalyzer.rule_based()
        text = '太郎は「おはよう」と言った。花子は微笑んだ。'
        results = analyzer.classify(text)
        types = {r.segment_type for r in results}
        assert "dialogue" in types
        assert "narration" in types

    def test_classify_returns_analysis_result(self):
        analyzer = TextAnalyzer.rule_based()
        results = analyzer.classify("テスト。")
        assert all(isinstance(r, AnalysisResult) for r in results)

    def test_classify_speaker_extraction(self):
        analyzer = TextAnalyzer.rule_based()
        results = analyzer.classify('太郎は「おはよう」と言った。')
        dialogue = [r for r in results if r.segment_type == "dialogue"]
        assert len(dialogue) >= 1
        # ルールベース話者抽出で太郎が検出される
        assert dialogue[0].speaker == "太郎"


class TestTextAnalyzerAnalyze:
    @pytest.mark.asyncio
    async def test_analyze_rule_based_fallback(self):
        """LLMなしの場合、ルールベースフォールバックで分析."""
        analyzer = TextAnalyzer.rule_based()
        results = await analyzer.analyze("太郎は「おはよう」と言った。")
        assert isinstance(results, list)
        assert len(results) >= 1
        assert all(isinstance(r, AnalysisResult) for r in results)
        types = {r.segment_type for r in results}
        assert "dialogue" in types

    @pytest.mark.asyncio
    async def test_analyze_with_known_characters(self):
        analyzer = TextAnalyzer.rule_based()
        results = await analyzer.analyze(
            "太郎は「おはよう」と言った。",
            known_characters=["太郎", "花子"],
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_analyze_empty_text(self):
        analyzer = TextAnalyzer.rule_based()
        results = await analyzer.analyze("")
        assert results == []

    @pytest.mark.asyncio
    async def test_analyze_scene_breaks(self):
        analyzer = TextAnalyzer.rule_based()
        text = "第一場面。\n\n***\n\n第二場面。"
        results = await analyzer.analyze(text)
        types = [r.segment_type for r in results]
        assert "scene_break" in types


class TestTextAnalyzerSync:
    def test_analyze_sync(self):
        analyzer = TextAnalyzer.rule_based()
        results = analyzer.analyze_sync("テスト。")
        assert isinstance(results, list)
        assert len(results) >= 1


class TestToAnalysisResult:
    def test_conversion(self):
        seg = AnalyzedSegment(
            text="テスト",
            type=SegmentType.DIALOGUE,
            index=0,
            speaker="太郎",
            scene="daily",
            emotion="happy",
            intensity=0.8,
        )
        result = _to_analysis_result(seg)
        assert isinstance(result, AnalysisResult)
        assert result.text == "テスト"
        assert result.segment_type == "dialogue"
        assert result.speaker == "太郎"
        assert result.scene == "daily"
        assert result.emotion == "happy"
        assert result.intensity == 0.8
