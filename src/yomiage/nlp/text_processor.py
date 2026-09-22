"""Text preprocessing — HTML cleanup, ruby handling, normalization."""

import re
import unicodedata
from importlib.resources.abc import Traversable
from pathlib import Path

from bs4 import BeautifulSoup

from ..resource_loader import packaged_config_resource
from .math_processor import MathProcessor

_DEFAULT_MATH_DICT = packaged_config_resource("math_dict.yaml")


class TextProcessor:
    """テキスト前処理."""

    def __init__(self, math_dict_path: Path | Traversable | None = None) -> None:
        self._math = MathProcessor(dict_path=math_dict_path or _DEFAULT_MATH_DICT)

    def process(self, text: str) -> str:
        """テキストをTTS用にクリーニング."""
        text = self._normalize(text)
        text = self._math.process_text(text)
        text = self._clean_aozora_markers(text)
        text = self._normalize_punctuation(text)
        text = self._clean_urls(text)
        text = self._clean_footnote_markers(text)
        text = self._clean_list_markers(text)
        text = self._clean_whitespace(text)
        return text

    async def process_async(self, text: str) -> str:
        """テキストをTTS用にクリーニング（LLMフォールバックあり）."""
        text = self._normalize(text)
        text = await self._math.process_text_async(text)
        text = self._clean_aozora_markers(text)
        text = self._normalize_punctuation(text)
        text = self._clean_urls(text)
        text = self._clean_footnote_markers(text)
        text = self._clean_list_markers(text)
        text = self._clean_whitespace(text)
        return text

    def process_html(self, html: str) -> str:
        """HTMLからテキストを抽出して前処理."""
        return self.process_document(extract_html_document(html)).synthesis_text

    def process_document(self, value):
        """原文と位置付き読みを保持して正規化する。数式変換は合成直前に行う。"""
        from ..core.text import STATIC_READINGS, ReadingSpan, TextDocument

        document = value if isinstance(value, TextDocument) else TextDocument.plain(value)
        document = _normalize_document(document)
        candidates = []
        for match in re.finditer(r"[A-Za-z][A-Za-z0-9_]*", document.text):
            term = match.group()
            if term in STATIC_READINGS:
                candidates.append(
                    ReadingSpan(
                        document.source_map[match.start()][0],
                        document.source_map[match.end() - 1][1],
                        document.original[
                            document.source_map[match.start()][0] : document.source_map[
                                match.end() - 1
                            ][1]
                        ],
                        STATIC_READINGS[term],
                        "common_dictionary",
                        "common",
                        "builtin:" + term,
                        "1",
                    )
                )
        return document.with_readings(candidates)

    def _normalize(self, text: str) -> str:
        # Unicode正規化 (NFKC)
        text = unicodedata.normalize("NFKC", text)
        return text

    def _clean_aozora_markers(self, text: str) -> str:
        # 青空文庫の注記を除去: ［＃...］
        text = re.sub(r"［＃[^］]*］", "", text)
        # 入力者注等
        text = re.sub(r"【[^】]*】", "", text)
        return text

    def _normalize_punctuation(self, text: str) -> str:
        # マークダウン記号除去
        text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        # 三点リーダー統一
        text = re.sub(r"\.{3,}", "…", text)
        text = re.sub(r"。{2,}", "。", text)
        # ダッシュ統一
        text = re.sub(r"[―—–]{2,}", "――", text)
        return text

    @staticmethod
    def has_alphabet(text: str) -> bool:
        """テキストにアルファベットが含まれるか."""
        return bool(re.search(r"[A-Za-z]", text))

    def _clean_urls(self, text: str) -> str:
        # URLを除去（URL構成文字のみマッチ、日本語で停止）
        return re.sub(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", "", text)

    def _clean_footnote_markers(self, text: str) -> str:
        # 脚注マーカーを除去: [1], [注1], *1
        text = re.sub(r"\[(?:注)?\d+\]", "", text)
        text = re.sub(r"\*\d+", "", text)
        return text

    def _clean_list_markers(self, text: str) -> str:
        # 行頭のリストマーカーを除去
        return re.sub(r"^\s*(?:\d+[.)]\s+|[-*+]\s+)", "", text, flags=re.MULTILINE)

    def _clean_whitespace(self, text: str) -> str:
        # 行頭の全角スペース（字下げ）を除去
        text = re.sub(r"^[　 ]+", "", text, flags=re.MULTILINE)
        # 連続空行を1つに
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def extract_html_document(html: str | object):
    """inline要素で改行を挿入せず、ルビの親文字と読みの位置を取得する。"""
    from bs4 import Comment, NavigableString, Tag

    from ..core.text import ReadingSpan, TextDocument

    root = BeautifulSoup(html, "lxml") if isinstance(html, str) else html
    pieces: list[str] = []
    readings: list[ReadingSpan] = []
    offset = 0
    blocks = {
        "p",
        "div",
        "article",
        "section",
        "main",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
    }

    def append(text: str) -> None:
        nonlocal offset
        pieces.append(text)
        offset += len(text)

    def walk(node) -> None:
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            append(str(node))
            return
        if not isinstance(node, Tag) or node.name in {"rt", "rp", "script", "style"}:
            return
        if node.name == "br":
            append("\n")
            return
        if node.name in blocks and pieces and not pieces[-1].endswith("\n"):
            append("\n")
        if node.name == "ruby":
            start = offset
            for child in node.children:
                if isinstance(child, Tag) and child.name == "rt":
                    surface = "".join(pieces)[start:offset]
                    reading = child.get_text().strip()
                    if surface and reading:
                        readings.append(ReadingSpan(start, offset, surface, reading, "ruby"))
                    start = offset
                else:
                    walk(child)
        else:
            for child in node.children:
                walk(child)
        if node.name in blocks and pieces and not pieces[-1].endswith("\n"):
            append("\n")

    walk(root)
    original = "".join(pieces)
    return TextDocument.plain(original).with_readings(readings)


def _normalize_document(document):
    """各変換時に範囲を伝播し、変換後の文字列から位置を推測しない。"""
    from dataclasses import replace

    text = document.text
    source_map = list(document.source_map)
    # NFKDの各文字の出所を保ち、結合文字とHangulをNFCへまとめる。
    units: list[tuple[str, tuple[int, int]]] = []
    for char, span in zip(text, source_map):
        units.extend((part, span) for part in unicodedata.normalize("NFKD", char))
    groups: list[list[tuple[str, tuple[int, int]]]] = []
    for unit in units:
        if groups:
            preceding = "".join(c for c, _ in groups[-1])
            composed = unicodedata.normalize("NFC", preceding)
            if (
                unicodedata.combining(unit[0])
                or len(unicodedata.normalize("NFC", composed + unit[0])) < len(composed) + 1
            ):
                groups[-1].append(unit)
                continue
        groups.append([unit])
    chars: list[str] = []
    source_map = []
    for group in groups:
        raw = "".join(c for c, _ in group)
        value = unicodedata.normalize("NFC", raw)
        span = (min(s[0] for _, s in group), max(s[1] for _, s in group))
        chars.append(value)
        source_map.extend([span] * len(value))
    text = "".join(chars)

    def sub(
        pattern: str, replacement: str = "", *, group: int | None = None, flags: int = 0
    ) -> None:
        nonlocal text, source_map
        output: list[str] = []
        mapping: list[tuple[int, int]] = []
        pos = 0
        for match in re.finditer(pattern, text, flags):
            a, b = match.span()
            output.append(text[pos:a])
            mapping.extend(source_map[pos:a])
            if group is not None:
                x, y = match.span(group)
                output.append(text[x:y])
                mapping.extend(source_map[x:y])
            else:
                output.append(replacement)
                if replacement:
                    mapping.extend([(source_map[a][0], source_map[b - 1][1])] * len(replacement))
            pos = b
        output.append(text[pos:])
        mapping.extend(source_map[pos:])
        text, source_map = "".join(output), mapping

    sub(r"\[#.*?\]|【[^】]*】")
    sub(r"\*{1,3}([^*]+)\*{1,3}", group=1)
    sub(r"^#{1,6}\s*", flags=re.MULTILINE)
    sub(r"\.{3,}", "…")
    sub(r"。{2,}", "。")
    sub(r"[―—–]{2,}", "――")
    sub(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
    sub(r"\[(?:注)?\d+\]|\*\d+")
    sub(r"^\s*(?:\d+[.)]\s+|[-*+]\s+)", flags=re.MULTILINE)
    sub(r"^[　 ]+", flags=re.MULTILINE)
    sub(r"\n{3,}", "\n\n")
    sub(r"^\s+|\s+$")
    return replace(document, text=text, source_map=tuple(source_map))
