"""Text preprocessing — HTML cleanup, ruby handling, normalization."""

import re
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup

from .math_processor import MathProcessor

_DEFAULT_MATH_DICT = (
    Path(__file__).parent.parent.parent.parent / "config" / "math_dict.yaml"
)


class TextProcessor:
    """テキスト前処理."""

    def __init__(self, math_dict_path: Path | None = None) -> None:
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
        # Pandoc math span を変換してから BS4 に渡す
        html = self._math.process_html_math(html)

        soup = BeautifulSoup(html, "lxml")

        # ルビ処理
        for ruby in soup.find_all("ruby"):
            rb = ruby.find("rb")
            if rb:
                ruby.replace_with(rb.get_text())

        text = soup.get_text("\n")
        return self.process(text)

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
