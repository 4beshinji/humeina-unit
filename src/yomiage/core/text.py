"""原文位置を基準にする読み指定と正規化本文。外部サービスには依存しない。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

READING_PRIORITY = {
    "explicit": 0,
    "ruby": 1,
    "work_dictionary": 2,
    "common_dictionary": 3,
    "llm": 4,
}


@dataclass(frozen=True, slots=True)
class ReadingSpan:
    """原文の一出現箇所への読み。endは範囲に含まない。"""

    start: int
    end: int
    surface: str
    reading: str
    origin: str = "explicit"
    scope: str = "occurrence"
    entry_id: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.start) is not int
            or type(self.end) is not int
            or not 0 <= self.start < self.end
        ):
            raise ValueError("invalid reading range")
        if not self.surface or not self.reading or not self.reading.strip():
            raise ValueError("surface and reading must not be empty")
        if self.origin not in READING_PRIORITY:
            raise ValueError(f"unknown reading origin: {self.origin}")

    def validate(self, original: str) -> None:
        if self.end > len(original) or original[self.start : self.end] != self.surface:
            raise ValueError("reading range does not match original surface")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ReadingSpan:
        return cls(**data)


@dataclass(frozen=True, slots=True)
class TextDocument:
    """表示原文、正規化文字ごとの原文範囲、採用した読みと競合を保持する。"""

    original: str
    text: str
    source_map: tuple[tuple[int, int], ...]
    readings: tuple[ReadingSpan, ...] = ()
    conflicts: tuple[ReadingSpan, ...] = ()
    reading_targets: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_map", tuple(tuple(pair) for pair in self.source_map))
        for name in ("readings", "conflicts"):
            object.__setattr__(
                self,
                name,
                tuple(
                    r if isinstance(r, ReadingSpan) else ReadingSpan.from_dict(r)
                    for r in getattr(self, name)
                ),
            )
        object.__setattr__(
            self, "reading_targets", tuple(tuple(pair) for pair in self.reading_targets)
        )
        for start, end in self.reading_targets:
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(self.original)
            ):
                raise ValueError("invalid pronunciation target range")
        if len(self.source_map) != len(self.text):
            raise ValueError("source_map must cover every normalized character")
        previous = 0
        for start, end in self.source_map:
            if (
                type(start) is not int
                or type(end) is not int
                or not previous <= start < end <= len(self.original)
            ):
                raise ValueError("invalid or unordered source_map")
            previous = start
        for reading in (*self.readings, *self.conflicts):
            reading.validate(self.original)
        ordered = sorted(self.readings, key=lambda r: r.start)
        if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError("accepted readings must not overlap")

    @classmethod
    def plain(cls, text: str, readings: tuple[ReadingSpan, ...] = ()) -> TextDocument:
        return cls(text, text, tuple((i, i + 1) for i in range(len(text))), readings)

    def with_readings(self, candidates: list[ReadingSpan]) -> TextDocument:
        """優先順位、長い語句、開始位置、辞書順で決定的に競合を解決する。"""
        accepted: list[ReadingSpan] = []
        conflicts = list(self.conflicts)
        unique = dict.fromkeys((*self.readings, *candidates))
        for r in sorted(
            unique,
            key=lambda r: (
                READING_PRIORITY[r.origin],
                bool(r.entry_id and r.entry_id.startswith("builtin:")),
                -(r.end - r.start),
                r.start,
                r.entry_id or "",
                r.reading,
            ),
        ):
            r.validate(self.original)
            if any(r.start < a.end and a.start < r.end for a in accepted):
                conflicts.append(r)
            else:
                accepted.append(r)
        return replace(
            self,
            readings=tuple(sorted(accepted, key=lambda r: r.start)),
            conflicts=tuple(dict.fromkeys(conflicts)),
        )

    def slice(self, start: int, end: int) -> TextDocument:
        """正規化本文の範囲を切り出す。読みや正規化の途中での分割は拒否する。"""
        if not 0 <= start < end <= len(self.text):
            raise ValueError("invalid text slice")
        a, b = self.source_map[start][0], self.source_map[end - 1][1]
        for r in self.readings:
            if r.start < b and a < r.end and not a <= r.start < r.end <= b:
                raise ValueError("text boundary cuts a reading")

        def local(items: tuple[ReadingSpan, ...]) -> tuple[ReadingSpan, ...]:
            return tuple(
                replace(r, start=r.start - a, end=r.end - a)
                for r in items
                if a <= r.start < r.end <= b
            )

        return TextDocument(
            self.original[a:b],
            self.text[start:end],
            tuple((x - a, y - a) for x, y in self.source_map[start:end]),
            local(self.readings),
            local(self.conflicts),
        )

    @property
    def synthesis_text(self) -> str:
        """位置が一致する出現箇所にだけ読みを適用する。原文は変更しない。"""
        out: list[str] = []
        pos = 0
        for r in self.readings:
            indices = [i for i, (a, b) in enumerate(self.source_map) if a < r.end and r.start < b]
            if not indices:
                continue
            start, end = indices[0], indices[-1] + 1
            if self.source_map[start][0] < r.start or self.source_map[end - 1][1] > r.end:
                raise ValueError("reading cuts a normalized character")
            out.extend((self.text[pos:start], r.reading))
            pos = end
        out.append(self.text[pos:])
        return "".join(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "text": self.text,
            "source_map": [list(pair) for pair in self.source_map],
            "readings": [r.to_dict() for r in self.readings],
            "conflicts": [r.to_dict() for r in self.conflicts],
            "reading_targets": [list(pair) for pair in self.reading_targets],
        }

    @classmethod
    def from_dict(cls, data: dict) -> TextDocument:
        return cls(**data)


STATIC_READINGS = {
    "AI": "エーアイ",
    "API": "エーピーアイ",
    "BBC": "ビービーシー",
    "CPU": "シーピーユー",
    "GPU": "ジーピーユー",
    "HTTP": "エイチティーティーピー",
    "HTTPS": "エイチティーティーピーエス",
    "JR": "ジェイアール",
    "LLM": "エルエルエム",
    "NLP": "エヌエルピー",
    "ROCm": "ロックエム",
    "TTS": "ティーティーエス",
    "URL": "ユーアールエル",
}
