"""Tests for speaker extraction."""

from yomiage.nlp.classifier import TextClassifier
from yomiage.nlp.speaker import SpeakerExtractor


def test_speaker_before_dialogue():
    c = TextClassifier()
    e = SpeakerExtractor()
    segments = c.classify("太郎は言った。「こんにちは」")
    segments = e.extract(segments)
    dialogue_segs = [s for s in segments if s.type.value == "dialogue"]
    # 太郎 should be detected as candidate
    assert any("太郎" in s.speaker_candidates for s in dialogue_segs)


def test_speaker_no_match():
    c = TextClassifier()
    e = SpeakerExtractor()
    segments = c.classify("「おはよう」")
    segments = e.extract(segments)
    dialogue_segs = [s for s in segments if s.type.value == "dialogue"]
    assert all(len(s.speaker_candidates) == 0 for s in dialogue_segs)


def test_speaker_multiple():
    c = TextClassifier()
    e = SpeakerExtractor()
    text = "太郎が叫んだ。「逃げろ！」花子は答えた。「分かった！」"
    segments = c.classify(text)
    segments = e.extract(segments)
    dialogue_segs = [s for s in segments if s.type.value == "dialogue"]
    speakers = set()
    for s in dialogue_segs:
        speakers.update(s.speaker_candidates)
    assert "太郎" in speakers
