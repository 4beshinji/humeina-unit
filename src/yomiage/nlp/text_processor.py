"""Text preprocessing — HTML cleanup, ruby handling, normalization."""

import re
import unicodedata

from bs4 import BeautifulSoup


class TextProcessor:
    """テキスト前処理."""

    def process(self, text: str) -> str:
        """テキストをTTS用にクリーニング."""
        text = self._normalize(text)
        text = self._clean_aozora_markers(text)
        text = self._normalize_punctuation(text)
        text = self._clean_whitespace(text)
        return text

    def process_html(self, html: str) -> str:
        """HTMLからテキストを抽出して前処理."""
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

    def _clean_whitespace(self, text: str) -> str:
        # 行頭の全角スペース（字下げ）を除去
        text = re.sub(r"^[　 ]+", "", text, flags=re.MULTILINE)
        # 連続空行を1つに
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
