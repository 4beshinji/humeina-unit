"""小説家になろう コンテンツソース."""

import asyncio
import re

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger

from .base import Chapter, ChapterInfo, ContentSource

# Rate limiting
_semaphore = asyncio.Semaphore(1)
_MIN_INTERVAL = 1.0


class NarouSource(ContentSource):
    """小説家になろうからの作品取得.

    対応URL形式:
    - https://ncode.syosetu.com/nXXXXXX/ (目次)
    - https://ncode.syosetu.com/nXXXXXX/1/ (個別話)
    """

    _NCODE_PATTERN = re.compile(r"ncode\.syosetu\.com/([Nn]\d+\w*)")

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "ncode.syosetu.com" in url or "syosetu.com" in url

    def _extract_ncode(self, url: str) -> str:
        m = self._NCODE_PATTERN.search(url)
        if not m:
            raise ValueError(f"Invalid Narou URL: {url}")
        return m.group(1).lower()

    def _extract_chapter_num(self, url: str) -> int | None:
        url = url.rstrip("/")
        parts = url.split("/")
        try:
            return int(parts[-1])
        except ValueError:
            return None

    async def _fetch_html(self, url: str) -> str:
        async with _semaphore:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "voisona_yomiage/0.1"},
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise RuntimeError(
                            f"Narou fetch failed: {resp.status} for {url}"
                        )
                    html = await resp.text()
            await asyncio.sleep(_MIN_INTERVAL)
            return html

    async def get_table_of_contents(self, work_url: str) -> list[ChapterInfo]:
        ncode = self._extract_ncode(work_url)
        toc_url = f"https://ncode.syosetu.com/{ncode}/"

        # 全ページの目次を取得
        chapters: list[ChapterInfo] = []
        page = 1
        while True:
            page_url = f"{toc_url}?p={page}" if page > 1 else toc_url
            html = await self._fetch_html(page_url)
            soup = BeautifulSoup(html, "lxml")

            # エピソードリンクを収集
            found = False
            for link in soup.find_all("a", href=True):
                href = link["href"]
                # /nXXXXXX/数字/ のパターン
                m = re.match(rf"/{ncode}/(\d+)/?$", href, re.IGNORECASE)
                if not m:
                    continue
                found = True
                idx = int(m.group(1))
                title = link.get_text(strip=True)
                full_url = f"https://ncode.syosetu.com{href}"
                if not any(c.url == full_url for c in chapters):
                    chapters.append(
                        ChapterInfo(title=title, url=full_url, index=idx)
                    )

            # 次ページがあるか
            next_link = soup.find("a", href=f"/{ncode}/?p={page + 1}")
            if next_link and found:
                page += 1
            else:
                break

        if not chapters:
            # 短編（単一ページ）の可能性
            return [ChapterInfo(title="本文", url=toc_url, index=0)]

        chapters.sort(key=lambda c: c.index)
        logger.info(f"Narou TOC: {len(chapters)} chapters")
        return chapters

    async def fetch_chapter(self, url: str) -> Chapter:
        self._extract_ncode(url)  # validate
        chapter_num = self._extract_chapter_num(url)

        # 目次URLの場合は第1話にリダイレクト
        if chapter_num is None:
            toc = await self.get_table_of_contents(url)
            if toc:
                return await self.fetch_chapter(toc[0].url)
            raise RuntimeError(f"No chapters found for {url}")

        logger.info(f"Fetching from Narou: {url}")
        html = await self._fetch_html(url)
        soup = BeautifulSoup(html, "lxml")

        # タイトル（新旧両方の構造に対応）
        title_tag = (
            soup.find("h1", class_="p-novel__title")
            or soup.find("p", class_="novel_subtitle")
            or soup.find("title")
        )
        title = title_tag.get_text(strip=True) if title_tag else "（タイトル不明）"

        # 本文（新旧両方の構造に対応）
        novel_view = (
            soup.find("div", class_="js-novel-text")
            or soup.find("div", class_="p-novel__text")
            or soup.find("div", class_="p-novel__body")
            or soup.find("div", id="novel_honbun")
            or soup.find("div", class_="novel_view")
        )
        if not novel_view:
            raise RuntimeError(f"Could not find novel text in {url}")

        # ルビ処理
        for ruby in novel_view.find_all("ruby"):
            rb = ruby.find("rb")
            if rb:
                ruby.replace_with(rb.get_text())

        text = novel_view.get_text("\n")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        return Chapter(
            title=title,
            text=text,
            raw_html=html,
            source_url=url,
            chapter_index=chapter_num,
        )

    async def get_next_chapter_url(self, current_url: str) -> str | None:
        ncode = self._extract_ncode(current_url)
        chapter_num = self._extract_chapter_num(current_url)

        if chapter_num is None:
            return None

        next_num = chapter_num + 1
        next_url = f"https://ncode.syosetu.com/{ncode}/{next_num}/"

        try:
            async with _semaphore:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "voisona_yomiage/0.1"},
                ) as session:
                    async with session.head(
                        next_url, allow_redirects=False
                    ) as resp:
                        if resp.status == 200:
                            await asyncio.sleep(_MIN_INTERVAL)
                            return next_url
        except Exception:
            pass
        return None
