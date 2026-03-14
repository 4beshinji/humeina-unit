"""Manifest data structures and JSON persistence for batch pipeline."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class ChapterMeta:
    """チャプターメタデータ."""

    index: int
    title: str
    url: str
    sentence_start: int  # このチャプターの最初のsentence index
    sentence_end: int  # このチャプターの最後のsentence index + 1


@dataclass
class SentenceEntry:
    """1文のエントリ."""

    index: int  # グローバル連番 (0001, 0002, ...)
    text: str
    chapter_index: int
    segment_type: str  # dialogue/narration/thought/scene_break
    speaker: str | None = None
    scene: str = "daily"
    emotion: str = "neutral"
    intensity: float = 0.5
    viewpoint_character: str | None = None
    tts_params: dict | None = None
    audio_file: str | None = None  # "0001.wav"
    status: str = "pending"  # pending/synthesized/failed


@dataclass
class BatchManifest:
    """バッチパイプライン全体のマニフェスト."""

    work_id: str
    work_title: str
    source_url: str
    mode: str  # "voisona" or "voicevox"
    chapters: list[ChapterMeta] = field(default_factory=list)
    characters: dict[str, dict] = field(default_factory=dict)
    sentences: list[SentenceEntry] = field(default_factory=list)
    analysis_complete: bool = False
    synthesis_complete: bool = False

    def output_dir(self, base_dir: Path) -> Path:
        return base_dir / self.work_id

    def save(self, base_dir: Path) -> Path:
        """マニフェストをJSONに保存."""
        out_dir = self.output_dir(base_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "manifest.json"

        data = asdict(self)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.debug(f"Manifest saved: {path}")
        return path

    @classmethod
    def load(cls, base_dir: Path, work_id: str) -> "BatchManifest":
        """JSONからマニフェストを読み込み."""
        path = base_dir / work_id / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")

        data = json.loads(path.read_text())

        chapters = [ChapterMeta(**ch) for ch in data.pop("chapters", [])]
        sentences = [SentenceEntry(**s) for s in data.pop("sentences", [])]

        manifest = cls(**data)
        manifest.chapters = chapters
        manifest.sentences = sentences
        return manifest

    @property
    def pending_sentences(self) -> list[SentenceEntry]:
        return [s for s in self.sentences if s.status == "pending"]

    @property
    def failed_sentences(self) -> list[SentenceEntry]:
        return [s for s in self.sentences if s.status == "failed"]

    @property
    def synthesized_count(self) -> int:
        return sum(1 for s in self.sentences if s.status == "synthesized")

    @property
    def total_count(self) -> int:
        return len(self.sentences)

    def progress_str(self) -> str:
        return f"{self.synthesized_count}/{self.total_count}"
