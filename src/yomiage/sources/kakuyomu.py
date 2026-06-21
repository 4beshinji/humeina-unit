"""カクヨム コンテンツソース."""

import asyncio
import re

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger

from .base import Chapter, ChapterInfo, ContentSource

_semaphore = asyncio.Semaphore(1)
_MIN_INTERVAL = 1.0


class KakuyomuSource(ContentSource):
    """カクヨムからの作品取得.

    対応URL形式:
    - https://kakuyomu.jp/works/NNNN (作品トップ)
    - https://kakuyomu.jp/works/NNNN/episodes/NNNN (個別エピソード)
    """

    _WORK_PATTERN = re.compile(r"kakuyomu\.jp/works/(\d+)")
    _EPISODE_PATTERN = re.compile(r"kakuyomu\.jp/works/(\d+)/episodes/(\d+)")

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "kakuyomu.jp" in url

    def _extract_work_id(self, url: str) -> str:
        m = self._WORK_PATTERN.search(url)
        if not m:
            raise ValueError(f"Invalid Kakuyomu URL: {url}")
        return m.group(1)

    def _extract_episode_id(self, url: str) -> str | None:
        m = self._EPISODE_PATTERN.search(url)
        return m.group(2) if m else None

    async def _fetch_html(self, url: str) -> str:
        async with _semaphore:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "humeina-unit/0.1"},
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Kakuyomu fetch failed: {resp.status} for {url}")
                    html = await resp.text()
            await asyncio.sleep(_MIN_INTERVAL)
            return html

    async def get_table_of_contents(self, work_url: str) -> list[ChapterInfo]:
        work_id = self._extract_work_id(work_url)
        toc_url = f"https://kakuyomu.jp/works/{work_id}"
        html = await self._fetch_html(toc_url)
        soup = BeautifulSoup(html, "lxml")

        chapters: list[ChapterInfo] = []
        # エピソード一覧
        for i, link in enumerate(soup.select("a[href*='/episodes/']")):
            href = link.get("href", "")
            if "/episodes/" not in href:
                continue
            title = link.get_text(strip=True)
            if not title:
                continue
            if href.startswith("/"):
                full_url = f"https://kakuyomu.jp{href}"
            else:
                full_url = href
            chapters.append(ChapterInfo(title=title, url=full_url, index=i))

        logger.info(f"Kakuyomu TOC: {len(chapters)} episodes")
        return chapters

    async def fetch_chapter(self, url: str) -> Chapter:
        work_id = self._extract_work_id(url)
        episode_id = self._extract_episode_id(url)

        # エピソードIDがない場合は目次の最初のエピソードを取得
        if not episode_id:
            toc = await self.get_table_of_contents(url)
            if not toc:
                raise RuntimeError(f"No episodes found for {url}")
            return await self.fetch_chapter(toc[0].url)

        logger.info(f"Fetching from Kakuyomu: {url}")
        html = await self._fetch_html(url)
        soup = BeautifulSoup(html, "lxml")

        # タイトル
        title_tag = soup.find("p", class_="widget-episodeTitle")
        if not title_tag:
            title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "（タイトル不明）"

        # 本文
        body = soup.find("div", class_="widget-episodeBody")
        if not body:
            raise RuntimeError(f"Could not find episode body in {url}")

        # ルビ処理
        for ruby in body.find_all("ruby"):
            rb = ruby.find("rb")
            if rb:
                ruby.replace_with(rb.get_text())

        text = body.get_text("\n")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        # チャプターインデックスをTOCから推定
        chapter_index = 0
        toc = await self.get_table_of_contents(f"https://kakuyomu.jp/works/{work_id}")
        for ch in toc:
            if episode_id in ch.url:
                chapter_index = ch.index
                break

        return Chapter(
            title=title,
            text=text,
            raw_html=html,
            source_url=url,
            chapter_index=chapter_index,
            total_chapters=len(toc) if toc else None,
        )

    async def get_next_chapter_url(self, current_url: str) -> str | None:
        work_id = self._extract_work_id(current_url)
        episode_id = self._extract_episode_id(current_url)
        if not episode_id:
            return None

        toc = await self.get_table_of_contents(f"https://kakuyomu.jp/works/{work_id}")
        for i, ch in enumerate(toc):
            if episode_id in ch.url and i + 1 < len(toc):
                return toc[i + 1].url
        return None
