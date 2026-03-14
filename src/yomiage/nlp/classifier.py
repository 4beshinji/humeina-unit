"""Rule-based text segment classifier."""

import re
from dataclasses import dataclass, field
from enum import Enum


class SegmentType(Enum):
    DIALOGUE = "dialogue"
    NARRATION = "narration"
    THOUGHT = "thought"
    SCENE_BREAK = "scene_break"


@dataclass
class TextSegment:
    """分類済みテキストセグメント."""

    text: str
    type: SegmentType
    index: int
    speaker_candidates: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class TextClassifier:
    """テキストをDIALOGUE/NARRATION/THOUGHT/SCENE_BREAKに分類."""

    _DIALOGUE_PATTERN = re.compile(r"「([^」]*)」")
    _THOUGHT_PATTERN = re.compile(r"[（(]([^）)]*)[）)]")
    _INNER_THOUGHT_PATTERN = re.compile(r"『([^』]*)』")
    _SCENE_BREAK_PATTERN = re.compile(
        r"^\s*(?:[＊\*]{3,}|[-ー－]{3,}|[□■◇◆]{3,})\s*$"
    )

    def classify(self, text: str) -> list[TextSegment]:
        """テキストをセグメントに分類."""
        lines = text.split("\n")
        segments: list[TextSegment] = []
        idx = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if self._SCENE_BREAK_PATTERN.match(line):
                segments.append(TextSegment(text="", type=SegmentType.SCENE_BREAK, index=idx))
                idx += 1
                continue

            # 行内の会話・心内語・地の文を分離
            parts = self._split_line(line)
            for part_text, part_type in parts:
                if part_text.strip():
                    segments.append(TextSegment(text=part_text, type=part_type, index=idx))
                    idx += 1

        return segments

    def _split_line(self, line: str) -> list[tuple[str, SegmentType]]:
        """行を会話・心内語・地の文に分離."""
        parts: list[tuple[str, SegmentType]] = []
        pos = 0

        while pos < len(line):
            # 「」 会話文を探す
            dialogue_match = self._DIALOGUE_PATTERN.search(line, pos)
            # （）心内語を探す
            thought_match = self._THOUGHT_PATTERN.search(line, pos)
            # 『』心内語を探す
            inner_match = self._INNER_THOUGHT_PATTERN.search(line, pos)

            # 最も早く現れるものを選択
            matches = []
            if dialogue_match:
                matches.append((dialogue_match.start(), dialogue_match, SegmentType.DIALOGUE))
            if thought_match:
                matches.append((thought_match.start(), thought_match, SegmentType.THOUGHT))
            if inner_match:
                matches.append((inner_match.start(), inner_match, SegmentType.THOUGHT))

            if not matches:
                remaining = line[pos:]
                if remaining.strip():
                    parts.append((remaining, SegmentType.NARRATION))
                break

            matches.sort(key=lambda x: x[0])
            earliest_pos, match, seg_type = matches[0]

            # マッチ前の地の文
            if earliest_pos > pos:
                before = line[pos:earliest_pos]
                if before.strip():
                    parts.append((before, SegmentType.NARRATION))

            # マッチした部分（括弧を含む全体）
            parts.append((match.group(0), seg_type))
            pos = match.end()

        return parts if parts else [(line, SegmentType.NARRATION)]
