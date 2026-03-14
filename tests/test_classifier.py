"""Tests for text classifier."""

from yomiage.nlp.classifier import SegmentType, TextClassifier


def test_classify_dialogue():
    c = TextClassifier()
    segments = c.classify("太郎は言った。「こんにちは」")
    types = [s.type for s in segments]
    assert SegmentType.NARRATION in types
    assert SegmentType.DIALOGUE in types


def test_classify_thought():
    c = TextClassifier()
    segments = c.classify("（これはまずい）と彼は思った。")
    types = [s.type for s in segments]
    assert SegmentType.THOUGHT in types


def test_classify_scene_break():
    c = TextClassifier()
    segments = c.classify("前のシーン。\n***\n次のシーン。")
    types = [s.type for s in segments]
    assert SegmentType.SCENE_BREAK in types


def test_classify_narration_only():
    c = TextClassifier()
    segments = c.classify("彼は静かに歩いていた。空は青かった。")
    assert all(s.type == SegmentType.NARRATION for s in segments)


def test_classify_inner_thought():
    c = TextClassifier()
    segments = c.classify("『本当にそうだろうか』と考えた。")
    types = [s.type for s in segments]
    assert SegmentType.THOUGHT in types


def test_classify_mixed():
    c = TextClassifier()
    text = "太郎は立ち上がった。「行くぞ」（もう限界だ）と思いながら歩き出した。"
    segments = c.classify(text)
    types = {s.type for s in segments}
    assert SegmentType.NARRATION in types
    assert SegmentType.DIALOGUE in types
    assert SegmentType.THOUGHT in types
