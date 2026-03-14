"""Bookmark management — JSON persistence."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

DEFAULT_BOOKMARK_DIR = Path("data/bookmarks")


@dataclass
class Bookmark:
    """Reading position bookmark."""

    source_url: str
    chapter_url: str
    chapter_index: int
    chunk_index: int
    title: str = ""
    work_title: str = ""


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
        path.write_text(json.dumps(asdict(bookmark), ensure_ascii=False, indent=2))
        logger.debug(f"Bookmark saved: {bookmark.title} ch={bookmark.chapter_index}")

    def load(self, source_url: str) -> Bookmark | None:
        path = self._path(source_url)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return Bookmark(**data)
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
            return Bookmark(**data)
        except Exception as e:
            logger.warning(f"Failed to load last bookmark: {e}")
            return None

    def delete(self, source_url: str) -> None:
        path = self._path(source_url)
        if path.exists():
            path.unlink()
