"""Bookmark management — JSON persistence."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from ..core.models import SourceRef
from ..utils.atomic_io import atomic_write_json

DEFAULT_BOOKMARK_DIR = Path("data/bookmarks")


@dataclass
class Bookmark:
    """Reading position bookmark.

    ``chunk_index`` is retained as a compatibility field for bookmarks written by the
    pre-plan reader.  New reader runs additionally persist the stable plan/utterance
    identity and the source span, so a text re-chunking does not silently move the
    resume position.
    """

    source_url: str
    chapter_url: str
    chapter_index: int
    chunk_index: int
    title: str = ""
    work_title: str = ""
    plan_id: str | None = None
    utterance_id: str | None = None
    source_ref: SourceRef | None = None

    def __post_init__(self) -> None:
        if self.source_ref is not None and not isinstance(self.source_ref, SourceRef):
            if not isinstance(self.source_ref, Mapping):
                raise TypeError("source_ref must be SourceRef or mapping")
            self.source_ref = SourceRef.from_dict(self.source_ref)
        if self.plan_id is not None:
            self.plan_id = str(self.plan_id).strip() or None
        if self.utterance_id is not None:
            self.utterance_id = str(self.utterance_id).strip() or None

    def to_dict(self) -> dict[str, Any]:
        """JSONへ保存する値へ明示的に変換する。旧JSONの形は変更しない。"""

        result: dict[str, Any] = {
            "source_url": self.source_url,
            "chapter_url": self.chapter_url,
            "chapter_index": self.chapter_index,
            "chunk_index": self.chunk_index,
            "title": self.title,
            "work_title": self.work_title,
        }
        if self.plan_id is not None:
            result["plan_id"] = self.plan_id
        if self.utterance_id is not None:
            result["utterance_id"] = self.utterance_id
        if self.source_ref is not None:
            result["source_ref"] = self.source_ref.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Bookmark":
        """旧bookmark（追加フィールドなし）も読み込める復元境界。"""

        return cls(
            source_url=str(data.get("source_url", "")),
            chapter_url=str(data.get("chapter_url", data.get("source_url", ""))),
            chapter_index=int(data.get("chapter_index", 0)),
            chunk_index=int(data.get("chunk_index", 0)),
            title=str(data.get("title", "")),
            work_title=str(data.get("work_title", "")),
            plan_id=data.get("plan_id"),
            utterance_id=data.get("utterance_id"),
            source_ref=data.get("source_ref"),
        )


class BookmarkManager:
    """ブックマーク管理（JSONファイル永続化）."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DEFAULT_BOOKMARK_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str) -> str:
        # URLからファイル名生成
        import hashlib

        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _path(self, url: str) -> Path:
        return self.data_dir / f"{self._key(url)}.json"

    def save(self, bookmark: Bookmark) -> None:
        path = self._path(bookmark.source_url)
        atomic_write_json(path, bookmark.to_dict())
        logger.debug(f"Bookmark saved: {bookmark.title} ch={bookmark.chapter_index}")

    def load(self, source_url: str) -> Bookmark | None:
        path = self._path(source_url)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return Bookmark.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load bookmark: {e}")
            return None

    def get_last(self) -> Bookmark | None:
        """最後に更新されたブックマークを返す."""
        files = sorted(self.data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        try:
            data = json.loads(files[0].read_text())
            return Bookmark.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load last bookmark: {e}")
            return None

    def delete(self, source_url: str) -> None:
        path = self._path(source_url)
        if path.exists():
            path.unlink()
