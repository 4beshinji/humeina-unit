"""Tests for Pipeline."""


import pytest

from yomiage.api.bridge import TTSBridge
from yomiage.api.config import AnalyzerConfig, LLMConfig, PipelineConfig, TTSEngineConfig
from yomiage.api.models import AnalysisResult, PipelineChunk, SynthesisResult
from yomiage.api.pipeline import Pipeline, _params_to_dict
from yomiage.tts.base import AudioResult, TTSParams, TTSProvider


class MockTTSProvider(TTSProvider):
    """テスト用モックTTSプロバイダー."""

    @property
    def name(self) -> str:
        return "mock"

    async def synthesize(self, text, voice="neutral", speed=1.0, **params):
        return AudioResult(
            audio_data=b"RIFF_mock", format="wav", sample_rate=24000, duration=1.0
        )

    async def is_available(self) -> bool:
        return True


def _make_pipeline_with_mock_tts() -> Pipeline:
    """モックTTSでPipelineを作成（LLMなし）."""
    config = PipelineConfig(
        tts=TTSEngineConfig(engine="voicevox"),
        analyzer=AnalyzerConfig(
            llm=LLMConfig(backend="ollama"),
            max_chunk_chars=200,
        ),
    )
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._config = config

    # Mock TTS bridge
    mock_provider = MockTTSProvider()
    pipeline._bridge = TTSBridge(mock_provider)

    # NLP pipeline (real, rule-based)
    from yomiage.nlp.pipeline import NLPAnalyzer
    from yomiage.reader.param_mapper import ParamMapper

    pipeline._analyzer = NLPAnalyzer.rule_based(max_chunk_chars=200)
    pipeline._param_mapper = ParamMapper()
    pipeline._character_db = None
    pipeline._voice_profile = None

    return pipeline


class TestPipelineProcess:
    @pytest.mark.asyncio
    async def test_process_simple_text(self):
        pipeline = _make_pipeline_with_mock_tts()
        results = await pipeline.process("太郎は「おはよう」と言った。")
        assert isinstance(results, list)
        assert len(results) >= 1
        for chunk in results:
            assert isinstance(chunk, PipelineChunk)
            assert isinstance(chunk.analysis, AnalysisResult)
            assert isinstance(chunk.audio, SynthesisResult)
            assert chunk.audio.audio_data == b"RIFF_mock"

    @pytest.mark.asyncio
    async def test_process_empty_text(self):
        pipeline = _make_pipeline_with_mock_tts()
        results = await pipeline.process("")
        assert results == []

    @pytest.mark.asyncio
    async def test_process_narration_only(self):
        pipeline = _make_pipeline_with_mock_tts()
        results = await pipeline.process("空は青く澄んでいた。風が優しく吹いていた。")
        assert len(results) >= 1
        assert results[0].analysis.segment_type == "narration"

    @pytest.mark.asyncio
    async def test_process_with_characters(self):
        pipeline = _make_pipeline_with_mock_tts()
        results = await pipeline.process(
            "太郎は「おはよう」と言った。",
            characters={
                "太郎": {"gender": "male", "age_group": "young_adult"},
            },
        )
        assert len(results) >= 1


class TestPipelineStream:
    @pytest.mark.asyncio
    async def test_stream(self):
        pipeline = _make_pipeline_with_mock_tts()
        chunks = []
        async for chunk in pipeline.stream("太郎は「おはよう」と言った。花子は微笑んだ。"):
            chunks.append(chunk)
        assert len(chunks) >= 1
        assert all(isinstance(c, PipelineChunk) for c in chunks)


class TestPipelineSync:
    def test_process_sync(self):
        pipeline = _make_pipeline_with_mock_tts()
        results = pipeline.process_sync("テスト文章。")
        assert isinstance(results, list)
        assert len(results) >= 1


class TestPipelineConfig:
    def test_from_dict(self):
        raw = {
            "tts": {"primary_provider": "voicevox", "lookahead_chunks": 5},
            "voicevox": {"url": "http://localhost:50021", "default_speaker": "47"},
            "ollama": {"url": "http://localhost:11434", "model": "qwen3.5:3b"},
        }
        config = PipelineConfig.from_dict(raw)
        assert config.tts.engine == "voicevox"
        assert config.tts.url == "http://localhost:50021"
        assert config.tts.default_voice == "47"
        assert config.analyzer.llm.backend == "ollama"
        assert config.lookahead == 5

    def test_from_dict_with_fallback(self):
        raw = {
            "tts": {
                "primary_provider": "voisona",
                "fallback_provider": "voicevox",
            },
            "voisona": {"url": "http://voisona:5000"},
            "voicevox": {"url": "http://voicevox:50021"},
        }
        config = PipelineConfig.from_dict(raw)
        assert config.tts.engine == "voisona"
        assert config.tts_fallback is not None
        assert config.tts_fallback.engine == "voicevox"

    def test_tts_engine_config_to_provider_dict(self):
        config = TTSEngineConfig(
            engine="voicevox",
            url="http://localhost:50021",
            default_voice="47",
        )
        d = config.to_provider_dict()
        assert d["url"] == "http://localhost:50021"
        assert d["default_speaker"] == "47"

    def test_tts_engine_config_voicepeak(self):
        config = TTSEngineConfig(
            engine="voicepeak",
            default_voice="Narrator",
        )
        d = config.to_provider_dict()
        assert d["default_narrator"] == "Narrator"


class TestParamsToDict:
    def test_default_params_empty(self):
        params = TTSParams()
        assert _params_to_dict(params) == {}

    def test_non_default_params(self):
        params = TTSParams(
            voice_id="47", speed=1.5, pitch=100.0, style_weights=[1.0, 0.0, 0.0, 0.0, 0.0]
        )
        d = _params_to_dict(params)
        assert d["voice_id"] == "47"
        assert d["speed"] == 1.5
        assert d["pitch"] == 100.0
        assert d["style_weights"] == [1.0, 0.0, 0.0, 0.0, 0.0]
