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
    _SCENE_BREAK_PATTERN = re.compile(r"^\s*(?:[＊\*]{3,}|[-ー－]{3,}|[□■◇◆]{3,})\s*$")

    def __init__(self, content_kind: str = "novel"):
        if content_kind not in {"plain", "novel", "script"}:
            raise ValueError("unknown content kind")
        self.content_kind = content_kind

    def classify(self, text: str) -> list[TextSegment]:
        """TTS分割より前に構造と位置を確定する。複数行・複数文の台詞を保つ。"""
        segments: list[TextSegment] = []

        def emit(start: int, end: int, kind: SegmentType) -> None:
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start == end:
                return
            value = text[start:end]
            if self._SCENE_BREAK_PATTERN.fullmatch(value):
                kind, value = SegmentType.SCENE_BREAK, ""
            segments.append(
                TextSegment(
                    value,
                    kind,
                    len(segments),
                    metadata={
                        "normalized_start": start,
                        "normalized_end": end,
                    },
                )
            )

        brackets = {"「": ("」", SegmentType.DIALOGUE)}
        if self.content_kind == "novel":
            brackets.update(
                {
                    "（": ("）", SegmentType.THOUGHT),
                    "(": (")", SegmentType.THOUGHT),
                    "『": ("』", SegmentType.THOUGHT),
                }
            )
        if self.content_kind == "plain":
            brackets = {}
        start = pos = 0
        while pos < len(text):
            if text[pos] in brackets:
                closing, kind = brackets[text[pos]]
                depth, end = 1, pos + 1
                while end < len(text) and depth:
                    if text[end] == text[pos]:
                        depth += 1
                    elif text[end] == closing:
                        depth -= 1
                    end += 1
                if depth == 0:
                    emit(start, pos, SegmentType.NARRATION)
                    emit(pos, end, kind)
                    start = pos = end
                    continue
            if text[pos] == "\n":
                emit(start, pos + 1, SegmentType.NARRATION)
                start = pos + 1
            pos += 1
        emit(start, len(text), SegmentType.NARRATION)
        return segments

    def _split_line(self, line: str) -> list[tuple[str, SegmentType]]:
        """旧呼び出し元向け。構造解析はclassifyに集約する。"""
        return [(s.text, s.type) for s in self.classify(line)]
