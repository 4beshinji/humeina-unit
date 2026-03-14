"""Adaptive text splitter for TTS synthesis."""

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    """テキストチャンク."""

    text: str
    index: int
    is_scene_break: bool = False


class TextSplitter:
    """テキストをTTS合成に適したチャンクに分割.

    分割の優先順位:
    1. シーン区切り（***, ---, 空行2つ以上）
    2. 段落区切り（空行）
    3. 文末（。！？）
    4. 読点（、）
    5. 強制分割（max_chars超過時）
    """

    def __init__(self, max_chars: int = 200):
        self.max_chars = max_chars
        self._scene_break_pattern = re.compile(
            r"^[\s]*[＊\*]{3,}[\s]*$|^[\s]*[-ー－]{3,}[\s]*$",
            re.MULTILINE,
        )

    def split(self, text: str) -> list[Chunk]:
        if not text.strip():
            return []

        # まずシーン区切りで分割
        scene_parts = self._split_scenes(text)

        chunks: list[Chunk] = []
        idx = 0
        for part, is_break in scene_parts:
            if is_break:
                chunks.append(Chunk(text="", index=idx, is_scene_break=True))
                idx += 1
                continue

            # 段落に分割
            paragraphs = self._split_paragraphs(part)
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                # 段落がmax_chars以下ならそのまま
                if len(para) <= self.max_chars:
                    chunks.append(Chunk(text=para, index=idx))
                    idx += 1
                else:
                    # 文で分割
                    sentences = self._split_sentences(para)
                    buffer = ""
                    for sent in sentences:
                        if len(buffer) + len(sent) <= self.max_chars:
                            buffer += sent
                        else:
                            if buffer:
                                chunks.append(Chunk(text=buffer, index=idx))
                                idx += 1
                            # 文自体がmax_charsを超える場合は読点で分割
                            if len(sent) > self.max_chars:
                                sub_parts = self._split_at_comma(sent)
                                for sp in sub_parts:
                                    chunks.append(Chunk(text=sp, index=idx))
                                    idx += 1
                            else:
                                buffer = sent
                    if buffer:
                        chunks.append(Chunk(text=buffer, index=idx))
                        idx += 1

        return chunks

    def split_sentences(self, text: str) -> list[str]:
        """テキストを文単位に分割（公開メソッド）."""
        paragraphs = self._split_paragraphs(text)
        sentences: list[str] = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            sentences.extend(self._split_sentences(para))
        return sentences

    def _split_scenes(self, text: str) -> list[tuple[str, bool]]:
        parts = self._scene_break_pattern.split(text)
        matches = list(self._scene_break_pattern.finditer(text))

        result: list[tuple[str, bool]] = []
        for i, part in enumerate(parts):
            if part.strip():
                result.append((part, False))
            if i < len(matches):
                result.append(("", True))
        return result if result else [(text, False)]

    def _split_paragraphs(self, text: str) -> list[str]:
        return re.split(r"\n\n+", text)

    def _split_sentences(self, text: str) -> list[str]:
        # 文末で分割（。！？の後）ただし括弧内は無視
        parts = re.split(r"((?:[^「」（）]*?[。！？!?]+))", text)
        sentences = []
        for p in parts:
            if p:
                sentences.append(p)
        # 隣接する短い断片を結合
        merged = []
        buf = ""
        for s in sentences:
            buf += s
            if re.search(r"[。！？!?]$", buf):
                merged.append(buf)
                buf = ""
        if buf:
            merged.append(buf)
        return merged if merged else [text]

    def _split_at_comma(self, text: str) -> list[str]:
        parts = re.split(r"((?:[^、，]*?[、，]))", text)
        result = []
        buf = ""
        for p in parts:
            if not p:
                continue
            if len(buf) + len(p) <= self.max_chars:
                buf += p
            else:
                if buf:
                    result.append(buf)
                buf = p
        if buf:
            result.append(buf)
        return result if result else [text]
