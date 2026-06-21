"""Timeline construction from manifest + WAV durations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..batch.manifest import BatchManifest, SentenceEntry
from ..tts.audio_utils import get_wav_duration


@dataclass
class TimelineEvent:
    """タイムライン上の1イベント（1文に対応）."""

    index: int
    start_time: float  # チャプター先頭からの累積秒数
    end_time: float
    text: str
    speaker: str | None
    scene: str
    emotion: str
    intensity: float
    segment_type: str
    audio_file: str | None


class TimelineBuilder:
    """manifest + WAVファイルからタイムラインを構築."""

    def __init__(self, manifest: BatchManifest, work_dir: Path):
        self.manifest = manifest
        self.work_dir = work_dir

    def build_chapter(self, chapter_index: int) -> list[TimelineEvent]:
        """チャプター単位でタイムラインを構築."""
        entries = [
            e for e in self.manifest.sentences
            if e.chapter_index == chapter_index and e.status == "synthesized"
        ]

        events: list[TimelineEvent] = []
        current_time = 0.0

        for entry in sorted(entries, key=lambda e: e.index):
            duration = self._resolve_duration(entry)
            if duration <= 0:
                continue

            event = TimelineEvent(
                index=entry.index,
                start_time=current_time,
                end_time=current_time + duration,
                text=entry.text,
                speaker=entry.speaker,
                scene=entry.scene,
                emotion=entry.emotion,
                intensity=entry.intensity,
                segment_type=entry.segment_type,
                audio_file=entry.audio_file,
            )
            events.append(event)
            current_time += duration

        logger.debug(
            f"Chapter {chapter_index}: {len(events)} events, "
            f"{current_time:.1f}s total"
        )
        return events

    def build_all(self) -> dict[int, list[TimelineEvent]]:
        """全チャプターのタイムラインを構築."""
        timelines: dict[int, list[TimelineEvent]] = {}
        for ch in self.manifest.chapters:
            events = self.build_chapter(ch.index)
            if events:
                timelines[ch.index] = events
        return timelines

    def _resolve_duration(self, entry: SentenceEntry) -> float:
        """エントリの音声長を解決."""
        # manifestにdurationが記録済みならそれを使う
        if entry.duration is not None:
            return entry.duration

        # WAVファイルから読み取る
        if entry.audio_file:
            wav_path = self.work_dir / entry.audio_file
            if wav_path.exists():
                duration = get_wav_duration(wav_path)
                entry.duration = duration
                return duration

        return 0.0
