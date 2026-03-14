"""Tests for text processor."""

from yomiage.nlp.text_processor import TextProcessor


def test_normalize_whitespace():
    p = TextProcessor()
    text = "　テスト\n\n\n\n段落2"
    result = p.process(text)
    assert result == "テスト\n\n段落2"


def test_clean_aozora_markers():
    p = TextProcessor()
    text = "本文です。［＃地付き］注記は除去。"
    result = p.process(text)
    assert "［＃" not in result
    assert "本文です。" in result


def test_normalize_punctuation():
    p = TextProcessor()
    text = "何だと...驚いた。"
    result = p.process(text)
    assert "…" in result


def test_empty():
    p = TextProcessor()
    assert p.process("") == ""
    assert p.process("   ") == ""
