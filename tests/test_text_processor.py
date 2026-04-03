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


def test_clean_urls():
    p = TextProcessor()
    text = "詳細はhttps://example.com/page?q=1を参照。"
    result = p.process(text)
    assert "https://" not in result
    assert "参照。" in result


def test_clean_footnote_markers():
    p = TextProcessor()
    text = "機能[1]の説明[注2]です。"
    result = p.process(text)
    assert "[1]" not in result
    assert "[注2]" not in result
    assert "機能の説明です。" in result


def test_clean_list_markers():
    p = TextProcessor()
    text = "1. 項目1\n2. 項目2\n- 箇条書き\n* アスタリスク"
    result = p.process(text)
    assert result.startswith("項目1")
    assert "箇条書き" in result
    assert "アスタリスク" in result
    # マーカーが除去されている
    assert "1. " not in result
    assert "- 箇条" not in result


def test_empty():
    p = TextProcessor()
    assert p.process("") == ""
    assert p.process("   ") == ""
