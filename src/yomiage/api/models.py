"""Public result types for the library API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SynthesisResult:
    """TTS合成結果."""

    audio_data: bytes
    format: str = "wav"
    sample_rate: int | None = None
    duration: float | None = None

    def save(self, path: str | Path) -> Path:
        """音声データをファイルに書き出す."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.audio_data)
        return p

    def convert(
        self,
        output_format: str,
        *,
        sample_rate: int | None = None,
        bitrate: str | None = None,
    ) -> "SynthesisResult":
        """音声データを指定フォーマットに変換.

        Args:
            output_format: 出力フォーマット（wav / mp3 / flac / ogg）
            sample_rate: 出力サンプリングレート
            bitrate: 出力ビットレート（例: "128k"）

        Returns:
            変換後の SynthesisResult
        """
        from .audio_format import convert_audio

        converted = convert_audio(
            self.audio_data,
            output_format,
            sample_rate=sample_rate,
            bitrate=bitrate,
        )
        return SynthesisResult(
            audio_data=converted,
            format=output_format,
            sample_rate=sample_rate or self.sample_rate,
            duration=self.duration,
        )


@dataclass
class VoiceInfo:
    """ボイス情報."""

    id: str
    name: str
    engine: str  # "voicevox" | "voisona" | "voicepeak"
    gender: str | None = None
    age_group: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class AnalysisResult:
    """テキスト分析結果."""

    text: str
    segment_type: str  # "dialogue" | "narration" | "thought" | "scene_break"
    speaker: str | None = None
    scene: str = "daily"
    emotion: str = "neutral"
    intensity: float = 0.5


@dataclass
class PipelineChunk:
    """パイプライン処理結果の1チャンク."""

    text: str
    analysis: AnalysisResult
    audio: SynthesisResult
    tts_params: dict = field(default_factory=dict)
