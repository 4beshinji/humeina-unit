"""青空文庫コンテンツソース."""

from bs4 import BeautifulSoup
from loguru import logger

from ..nlp.text_processor import extract_html_document
from ..ports.sources import URLPolicy
from .base import Chapter, ContentSource
from .http import HTTPFetcher


class AozoraSource(ContentSource):
    """青空文庫からの作品取得.

    対応URL形式:
    - https://www.aozora.gr.jp/cards/NNNNNN/files/NNNN_NNNNN.html
    """

    def __init__(
        self,
        fetcher: HTTPFetcher | None = None,
        *,
        policy: URLPolicy | None = None,
    ) -> None:
        super().__init__(fetcher, policy=policy)

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return _is_host(url, {"aozora.gr.jp", "www.aozora.gr.jp"})

    async def fetch_chapter(self, url: str) -> Chapter:
        logger.info(f"Fetching from Aozora: {url}")

        response = await self._fetcher.fetch(url, headers={"User-Agent": "humeina-unit/0.1"})
        if response.status != 200:
            raise RuntimeError(f"Aozora fetch failed: {response.status}")
        # 青空文庫はShift-JIS or UTF-8
        raw_html = self._decode(response.body)

        title, document = self._parse_document(raw_html)
        text = document.text
        logger.info(f"Fetched: {title} ({len(text)} chars)")

        return Chapter(
            title=title,
            text=text,
            raw_html=raw_html,
            source_url=url,
            chapter_index=0,
            total_chapters=1,
            document=document,
        )

    def _decode(self, data: bytes) -> str:
        for enc in ("utf-8", "shift_jis", "euc-jp"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")

    def _parse(self, html: str) -> tuple[str, str]:
        title, document = self._parse_document(html)
        return title, document.text

    def _parse_document(self, html: str):
        soup = BeautifulSoup(html, "lxml")

        # タイトル取得
        title_tag = soup.find("h1", class_="title")
        if not title_tag:
            title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "（タイトル不明）"

        # 本文取得
        main_text = soup.find("div", class_="main_text")
        if not main_text:
            # 古いフォーマットでは body 直下
            main_text = soup.find("body")

        if not main_text:
            return title, extract_html_document("")

        # 注記除去
        for note in main_text.find_all("span", class_="notes"):
            note.decompose()
        for note in main_text.find_all("div", class_="bibliographical_information"):
            note.decompose()

        return title, extract_html_document(main_text)


def _is_host(url: str, hosts: set[str]) -> bool:
    from urllib.parse import urlsplit

    try:
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return hostname in hosts
