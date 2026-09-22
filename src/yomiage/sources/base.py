"""Content source abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.text import TextDocument
from ..ports.sources import URLPolicy
from .http import HTTPFetcher


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
    document: TextDocument | None = None


class ContentSource(ABC):
    """コンテンツソース抽象基底クラス."""

    def __init__(
        self,
        fetcher: HTTPFetcher | None = None,
        *,
        policy: URLPolicy | None = None,
    ) -> None:
        """共通fetcherを注入する.

        テストではfake transport/resolverを持つfetcherを渡せる。省略時も必ず同じ
        URL policyを通る既定fetcherを生成し、サイト別実装が直接HTTPへ接続しない。
        """

        if fetcher is not None and policy is not None:
            raise ValueError("pass either fetcher or policy, not both")
        self._fetcher = fetcher or HTTPFetcher(policy=policy)

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
