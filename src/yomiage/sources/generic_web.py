"""汎用Webページ コンテンツソース（フォールバック）."""

import re

import aiohttp
from bs4 import BeautifulSoup, NavigableString, Tag
from loguru import logger

from .base import Chapter, ContentSource

_CODE_BLOCK_PLACEHOLDER = "コードブロック省略。"
_MAX_TABLE_ROWS = 5

# ノイズ要素のCSSセレクタ
_NOISE_SELECTORS = [
    "script", "style", "noscript", "iframe",
    "nav", "header", "footer", "aside",
    ".sidebar", ".widget", "[class*='hatena-module']",
    "#comments", ".comments", ".comment-area", ".comment-box",
    "[class*='ad-']", "[class*='advertisement']",
    "[id*='ad-']", "#google_afc_user", ".google-afc-user-container",
    ".breadcrumbs", ".breadcrumb",
    ".social-share", ".share-buttons", ".sns-follow",
    ".related-posts", ".pagination",
    ".author-bio",
    ".customized-footer",
]

# 本文コンテナ候補（優先順）
_ARTICLE_SELECTORS = [
    "article",
    "main",
    "[role='main']",
    ".post-content",
    ".article-body",
    ".entry-content",
    ".hentry",
    ".post-body",
    ".blog-content",
]


class GenericWebSource(ContentSource):
    """汎用Webページからの本文抽出（フォールバック）.

    レジストリの最後尾に配置し、特定サイト用ソースが
    マッチしなかった場合にのみ使われる。
    """

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith(("http://", "https://"))

    async def fetch_chapter(self, url: str) -> Chapter:
        logger.info(f"Fetching generic web page: {url}")
        html = await self._fetch_html(url)
        soup = BeautifulSoup(html, "lxml")

        title = self._extract_title(soup)

        # trafilatura で本文抽出を試行 → 失敗時は BS4 フォールバック
        text = self._extract_article(html, url)
        if not text or len(text.strip()) < 50:
            logger.debug("trafilatura extraction insufficient, falling back to BS4")
            text = self._extract_article_bs4(soup)

        if not text or not text.strip():
            raise RuntimeError(f"Could not extract article content from {url}")

        return Chapter(
            title=title,
            text=text,
            raw_html=html,
            source_url=url,
            chapter_index=0,
        )

    async def _fetch_html(self, url: str) -> str:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "voisona_yomiage/0.1"},
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Web fetch failed: {resp.status} for {url}")
                return await resp.text()

    def _extract_title(self, soup: BeautifulSoup) -> str:
        # og:title → h1 → <title> の優先順
        # og:title は記事タイトルを正確に含むことが多い
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()

        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)

        title = soup.find("title")
        if title:
            return title.get_text(strip=True)

        return "（タイトル不明）"

    def _extract_article(self, html: str, url: str) -> str | None:
        """trafilatura で本文抽出."""
        try:
            import trafilatura

            result = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
            return result
        except ImportError:
            logger.debug("trafilatura not installed, using BS4 fallback")
            return None
        except Exception as e:
            logger.warning(f"trafilatura extraction failed: {e}")
            return None

    def _extract_article_bs4(self, soup: BeautifulSoup) -> str:
        """BeautifulSoup ヒューリスティクスで本文抽出."""
        # ノイズ除去
        self._remove_noise(soup)

        # 本文コンテナを探す
        container = None
        for selector in _ARTICLE_SELECTORS:
            container = soup.select_one(selector)
            if container:
                break

        if not container:
            # テキスト量最大の div を使う
            container = self._find_largest_text_div(soup)

        if not container:
            container = soup.body or soup

        # TTS 用クリーニング
        return self._clean_for_tts(container)

    def _find_largest_text_div(self, soup: BeautifulSoup) -> Tag | None:
        best = None
        best_len = 0
        for div in soup.find_all("div"):
            text_len = len(div.get_text(strip=True))
            if text_len > best_len:
                best_len = text_len
                best = div
        return best

    def _clean_for_tts(self, container: Tag) -> str:
        """HTML コンテナを TTS 用テキストに変換."""
        # 1. ノイズ除去（コンテナ内にも残っている可能性）
        self._remove_noise(container)

        # 2. コードブロック置換
        self._replace_code_blocks(container)

        # 3. テーブル処理
        self._linearize_tables(container)

        # 4. 見出し変換
        self._handle_headings(container)

        # 5. リスト処理
        self._handle_lists(container)

        # 6. ルビ処理
        self._handle_ruby(container)

        # 7. 画像除去
        for img in container.find_all("img"):
            img.decompose()

        # テキスト抽出
        text = container.get_text("\n")
        # 連続空行を整理
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _remove_noise(self, soup: BeautifulSoup | Tag) -> None:
        for selector in _NOISE_SELECTORS:
            for el in soup.select(selector):
                el.decompose()

    def _replace_code_blocks(self, soup: Tag) -> None:
        for pre in soup.find_all("pre"):
            text_len = len(pre.get_text())
            if text_len > 50:
                pre.replace_with(NavigableString(f"\n{_CODE_BLOCK_PLACEHOLDER}\n"))
            else:
                # 短いコードはテキストを維持
                pre.replace_with(NavigableString(pre.get_text()))

    def _linearize_tables(self, soup: Tag) -> None:
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                table.decompose()
                continue

            if len(rows) > _MAX_TABLE_ROWS:
                cols = max(
                    (len(row.find_all(["td", "th"])) for row in rows), default=0
                )
                table.replace_with(
                    NavigableString(f"\n表省略、{len(rows)}行{cols}列。\n")
                )
            else:
                # ヘッダ行を取得
                headers = [
                    th.get_text(strip=True) for th in rows[0].find_all("th")
                ]
                lines: list[str] = []
                for row in rows:
                    cells = [
                        c.get_text(strip=True) for c in row.find_all(["td", "th"])
                    ]
                    if headers and row.find("td"):
                        pairs = [
                            f"{h}は{c}" for h, c in zip(headers, cells) if c
                        ]
                        if pairs:
                            lines.append("、".join(pairs) + "。")
                    elif cells:
                        lines.append("、".join(c for c in cells if c) + "。")
                table.replace_with(NavigableString("\n" + "\n".join(lines) + "\n"))

    def _handle_headings(self, soup: Tag) -> None:
        for tag_name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            for heading in soup.find_all(tag_name):
                text = heading.get_text(strip=True)
                if text:
                    heading.replace_with(NavigableString(f"\n\n{text}\n\n"))
                else:
                    heading.decompose()

    def _handle_lists(self, soup: Tag) -> None:
        for li in soup.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                # 末尾に句点がなければ追加
                if not text.endswith(("。", "、", ".", "!", "！", "?", "？")):
                    text += "。"
                li.replace_with(NavigableString(text + "\n"))
            else:
                li.decompose()

    def _handle_ruby(self, soup: Tag) -> None:
        for ruby in soup.find_all("ruby"):
            rb = ruby.find("rb")
            if rb:
                ruby.replace_with(NavigableString(rb.get_text()))
