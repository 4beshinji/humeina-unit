"""Content source abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChapterInfo:
    """目次エントリ."""

    title: str
    url: str
    index: int


@dataclass
class Chapter:
    """取得済みチャプター."""

    title: str
    text: str
    raw_html: str
    source_url: str
    chapter_index: int
    total_chapters: int | None = None


class ContentSource(ABC):
    """コンテンツソース抽象基底クラス."""

    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        """このソースがURLを処理可能か."""
        ...

    @abstractmethod
    async def fetch_chapter(self, url: str) -> Chapter:
        """指定URLのチャプターを取得."""
        ...

    async def get_next_chapter_url(self, current_url: str) -> str | None:
        """次のチャプターURLを返す. なければNone."""
        return None

    async def get_table_of_contents(self, work_url: str) -> list[ChapterInfo]:
        """目次一覧を返す."""
        return []
