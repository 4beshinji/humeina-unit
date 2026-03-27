"""Studio module data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScriptLine:
    """台本の1行（1発話）."""

    index: int  # 0-based global sequence
    speaker: str  # 話者名
    text: str  # 正規化済みテキスト
    original_text: str  # 元テキスト（.txtファイル用）
    emotion: str = "neutral"  # 感情タグ
    pause_after: float | None = None  # 後続ポーズ秒数（None=デフォルト使用）
    tts_params: dict | None = None  # 行単位のパラメータ上書き


@dataclass
class SpeakerMapping:
    """話者→TTSプロバイダーのマッピング."""

    speaker: str
    provider: str  # "voicevox" / "voisona" / "voicepeak"
    voice_id: str  # プロバイダー固有のID
    base_params: dict = field(default_factory=dict)  # speed, pitch等


@dataclass
class SynthResult:
    """合成結果."""

    line_index: int
    wav_path: Path
    txt_path: Path | None  # YMM4モードのみ
    duration: float
    speaker: str
    text: str


@dataclass
class StudioProject:
    """Studioプロジェクト全体."""

    name: str
    output_dir: Path
    lines: list[ScriptLine]
    speaker_mappings: dict[str, SpeakerMapping]
    results: list[SynthResult] = field(default_factory=list)
    default_pause: float = 0.3
    output_format: str = "ymm4"  # "ymm4" / "plain"
