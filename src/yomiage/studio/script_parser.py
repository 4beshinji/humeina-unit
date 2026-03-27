"""Script parser — plain text, CSV, JSON formats."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from ..nlp.splitter import TextSplitter
from ..nlp.text_processor import TextProcessor
from .models import ScriptLine

# ポーズマーカーパターン
_PAUSE_PATTERN = re.compile(r"^[（(]\s*(間|ポーズ|pause)\s*[)）]$", re.IGNORECASE)

# 話者行パターン: 「話者名: テキスト」（全角・半角コロン対応）
_SPEAKER_LINE_PATTERN = re.compile(r"^(.+?)[：:]\s*(.+)$")


class ScriptParser:
    """台本パーサー（plain/CSV/JSON）."""

    def __init__(
        self,
        text_processor: TextProcessor | None = None,
        splitter: TextSplitter | None = None,
        max_chars: int = 200,
    ):
        self.text_processor = text_processor or TextProcessor()
        self.splitter = splitter or TextSplitter(max_chars=max_chars)
        self.max_chars = max_chars

    def parse(self, path: Path) -> list[ScriptLine]:
        """ファイル形式を自動検出してパース."""
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return self.parse_csv(path)
        if suffix == ".json":
            return self.parse_json(path)
        # .txt or anything else → plain text
        content = path.read_text(encoding="utf-8")
        return self.parse_text(content)

    def parse_text(self, content: str) -> list[ScriptLine]:
        """プレーンテキスト台本をパース."""
        raw_lines = content.splitlines()
        entries: list[dict] = []
        current_speaker: str | None = None

        for raw_line in raw_lines:
            stripped = raw_line.strip()

            # コメント行
            if stripped.startswith("#"):
                continue

            # 空行・ポーズマーカー
            if not stripped or _PAUSE_PATTERN.match(stripped):
                if entries:
                    entries[-1]["pause"] = True
                continue

            # 話者行
            m = _SPEAKER_LINE_PATTERN.match(stripped)
            if m:
                current_speaker = m.group(1).strip()
                text = m.group(2).strip()
                entries.append({
                    "speaker": current_speaker,
                    "text": text,
                    "original": text,
                    "pause": False,
                })
            elif current_speaker:
                # コロンなし行 → 前の話者の続き
                entries.append({
                    "speaker": current_speaker,
                    "text": stripped,
                    "original": stripped,
                    "pause": False,
                })

        return self._build_lines(entries)

    def parse_csv(self, path: Path) -> list[ScriptLine]:
        """CSVファイルをパース（speaker,text[,emotion]ヘッダー付き）."""
        content = path.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(content))
        entries: list[dict] = []

        for row in reader:
            speaker = row.get("speaker", "").strip()
            text = row.get("text", "").strip()
            if not speaker or not text:
                continue
            entries.append({
                "speaker": speaker,
                "text": text,
                "original": text,
                "emotion": row.get("emotion", "neutral").strip() or "neutral",
                "pause": False,
            })

        return self._build_lines(entries)

    def parse_json(self, path: Path) -> list[ScriptLine]:
        """JSONファイルをパース."""
        data = json.loads(path.read_text(encoding="utf-8"))
        entries: list[dict] = []

        for item in data:
            speaker = item.get("speaker", "").strip()
            text = item.get("text", "").strip()
            if not speaker or not text:
                continue
            entries.append({
                "speaker": speaker,
                "text": text,
                "original": text,
                "emotion": item.get("emotion", "neutral") or "neutral",
                "pause": False,
            })

        return self._build_lines(entries)

    def _build_lines(self, entries: list[dict]) -> list[ScriptLine]:
        """エントリリストからScriptLineリストを構築（正規化＋分割）."""
        lines: list[ScriptLine] = []
        idx = 0

        for i, entry in enumerate(entries):
            # テキスト正規化
            processed = self.text_processor.process(entry["text"])
            if not processed:
                continue

            # 長い行はsplitterで分割
            if len(processed) > self.max_chars:
                chunks = self.splitter.split(processed)
                for chunk in chunks:
                    if not chunk.text or chunk.is_scene_break:
                        continue
                    lines.append(ScriptLine(
                        index=idx,
                        speaker=entry["speaker"],
                        text=chunk.text,
                        original_text=entry["original"],
                        emotion=entry.get("emotion", "neutral"),
                    ))
                    idx += 1
            else:
                lines.append(ScriptLine(
                    index=idx,
                    speaker=entry["speaker"],
                    text=processed,
                    original_text=entry["original"],
                    emotion=entry.get("emotion", "neutral"),
                ))
                idx += 1

            # ポーズマーカー → 直前の行にpause_afterを設定
            if entry.get("pause") and lines:
                lines[-1].pause_after = -1.0  # sentinel: use default_pause

        return lines
