"""Tests for text splitter."""

from yomiage.nlp.splitter import TextSplitter


def test_split_simple():
    s = TextSplitter(max_chars=200)
    chunks = s.split("これはテストです。もう一つの文です。")
    assert len(chunks) >= 1
    assert chunks[0].text == "これはテストです。もう一つの文です。"


def test_split_long_text():
    s = TextSplitter(max_chars=20)
    text = "短い文です。" * 10
    chunks = s.split(text)
    assert len(chunks) > 1
    for chunk in chunks:
        if not chunk.is_scene_break:
            assert len(chunk.text) <= 30  # Some margin for sentence boundaries


def test_split_scene_break():
    s = TextSplitter(max_chars=200)
    text = "最初の段落。\n\n***\n\n次の段落。"
    chunks = s.split(text)
    scene_breaks = [c for c in chunks if c.is_scene_break]
    assert len(scene_breaks) >= 1


def test_split_empty():
    s = TextSplitter()
    assert s.split("") == []
    assert s.split("   ") == []


def test_split_dialogue():
    s = TextSplitter(max_chars=200)
    text = "太郎は言った。\n「こんにちは」\n花子が答えた。\n「はい、こんにちは」"
    chunks = s.split(text)
    assert len(chunks) >= 1
    full_text = "".join(c.text for c in chunks if not c.is_scene_break)
    assert "太郎" in full_text
    assert "花子" in full_text
