"""humeina-unit — 高品質な音声読み上げシステム."""

__version__ = "0.1.0"

from yomiage.api import (
    AnalysisResult,
    AnalyzerConfig,
    LLMConfig,
    Pipeline,
    PipelineChunk,
    PipelineConfig,
    SynthesisResult,
    TextAnalyzer,
    TTSBridge,
    TTSEngineConfig,
    VoiceInfo,
)
from yomiage.tts.base import AudioResult, TTSParams, TTSProvider

__all__ = [
    # Tier 1: TTS Bridge
    "TTSBridge",
    "SynthesisResult",
    "VoiceInfo",
    # Tier 2: NLP Analysis
    "TextAnalyzer",
    "AnalysisResult",
    # Tier 3: Full Pipeline
    "Pipeline",
    "PipelineChunk",
    # Configuration
    "PipelineConfig",
    "TTSEngineConfig",
    "AnalyzerConfig",
    "LLMConfig",
    # Core types (advanced)
    "AudioResult",
    "TTSParams",
    "TTSProvider",
]
