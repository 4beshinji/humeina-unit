"""Planの読み指定から合成用本文を生成する共通境界。表示本文は変更しない。"""

from __future__ import annotations

from functools import lru_cache

from ..core.models import Utterance
from ..core.text import TextDocument
from ..nlp.math_processor import MathProcessor


@lru_cache(maxsize=1)
def _math_processor() -> MathProcessor:
    return MathProcessor()


def render_speech_text(value: Utterance | TextDocument) -> str:
    """位置付き読みの適用後に、既存の数式辞書・規則を一度適用する。"""
    return _math_processor().process_text(value.synthesis_text)
