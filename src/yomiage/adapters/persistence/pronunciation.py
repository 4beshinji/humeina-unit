"""確認済みの共通・作品辞書と、未確認候補のYAML保存。単一更新元向け。"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from ...core.text import ReadingSpan, TextDocument


@dataclass(frozen=True)
class DictionaryEntry:
    id: str
    surface: str
    reading: str
    version: str = "1"
    before: str | None = None
    after: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "surface", "reading", "version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"dictionary {name} must be a non-empty string")
        for name in ("before", "after"):
            if getattr(self, name) is not None and not isinstance(getattr(self, name), str):
                raise ValueError(f"dictionary {name} must be a literal string")


class PronunciationDictionary:
    """大小文字と前後のリテラル条件を区別する。LLM候補は読み込まない。"""

    def __init__(self, common: Path | str | None = None, work: Path | str | None = None):
        self.paths = [(Path(p), scope) for p, scope in ((common, "common"), (work, "work")) if p]

    @staticmethod
    def load(path: Path) -> tuple[DictionaryEntry, ...]:
        if not path.exists():
            return ()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError(f"invalid pronunciation dictionary: {path}")
        if raw.get("status", "confirmed") != "confirmed":
            raise ValueError("unconfirmed proposals cannot be used as a dictionary")
        entries = tuple(DictionaryEntry(**entry) for entry in raw.get("entries", []))
        if len({e.id for e in entries}) != len(entries):
            raise ValueError("duplicate dictionary entry id")
        return entries

    @staticmethod
    def save(path: Path, entries: list[DictionaryEntry], *, confirmed: bool = True) -> None:
        # 書き込み前に全件検証する。既存ファイルはatomic replaceまで変更しない。
        entries = [DictionaryEntry(**asdict(entry)) for entry in entries]
        if len({e.id for e in entries}) != len(entries):
            raise ValueError("duplicate dictionary entry id")
        payload = {
            "schema_version": 1,
            "status": "confirmed" if confirmed else "proposed",
            "entries": [asdict(entry) for entry in entries],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def apply(self, document: TextDocument) -> TextDocument:
        candidates: list[ReadingSpan] = []
        for path, scope in self.paths:
            for entry in self.load(path):
                for match in re.finditer(re.escape(entry.surface), document.original):
                    a, b = match.span()
                    if entry.before is not None and not document.original[:a].endswith(
                        entry.before
                    ):
                        continue
                    if entry.after is not None and not document.original[b:].startswith(
                        entry.after
                    ):
                        continue
                    # 英字単語の部分一致で別単語を壊さない。
                    if re.fullmatch(r"[A-Za-z0-9_]+", entry.surface):
                        if (a and re.match(r"[A-Za-z0-9_]", document.original[a - 1])) or (
                            b < len(document.original)
                            and re.match(r"[A-Za-z0-9_]", document.original[b])
                        ):
                            continue
                    candidates.append(
                        ReadingSpan(
                            a,
                            b,
                            entry.surface,
                            entry.reading,
                            f"{scope}_dictionary",
                            scope,
                            entry.id,
                            entry.version,
                        )
                    )
        return document.with_readings(candidates)
