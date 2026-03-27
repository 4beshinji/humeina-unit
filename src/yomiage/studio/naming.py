"""File naming strategies for studio output."""

from __future__ import annotations

import re

from .models import ScriptLine


class FileNamer:
    """ファイル命名（YMM4互換/plain連番）."""

    def __init__(self, format: str = "ymm4", max_slug_chars: int = 15):
        self.format = format
        self.max_slug_chars = max_slug_chars

    def wav_name(self, line: ScriptLine) -> str:
        """WAVファイル名を生成."""
        prefix = f"{line.index + 1:03d}"
        if self.format == "ymm4":
            slug = self._make_slug(line.text)
            return f"{prefix}_{line.speaker}_{slug}.wav"
        return f"{prefix}.wav"

    def txt_name(self, line: ScriptLine) -> str | None:
        """YMM4用テキストファイル名を生成."""
        if self.format != "ymm4":
            return None
        prefix = f"{line.index + 1:03d}"
        slug = self._make_slug(line.text)
        return f"{prefix}_{line.speaker}_{slug}.txt"

    def _make_slug(self, text: str) -> str:
        """テキストからファイル名安全なスラッグを生成."""
        # FS unsafe characters を除去
        slug = re.sub(r'[\\/:*?"<>|\n\r\t]', "", text)
        # 先頭・末尾の空白除去
        slug = slug.strip()
        # max_slug_chars で切り詰め
        if len(slug) > self.max_slug_chars:
            slug = slug[: self.max_slug_chars]
        return slug
