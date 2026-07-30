"""DOM-001 core model contract tests."""

from __future__ import annotations

import pytest

from yomiage.core.models import (
    Emotion,
    PlanWarning,
    SegmentType,
    SourceRef,
    Utterance,
    VoiceIntent,
)


def test_utterance_accepts_domain_values_and_is_immutable() -> None:
    intent = VoiceIntent(
        archetype="female_young",
        gender_hint="female",
        age_hint="young_adult",
        speaking_rate_ratio=1.05,
        emotion="happy",
        intensity=0.7,
        preferred_profile="warm",
    )
    source = SourceRef(
        source_url="https://example.test/chapter/1",
        chapter_index=1,
        start_offset=4,
        end_offset=10,
    )
    utterance = Utterance(
        id="u-001",
        order=0,
        text="こんにちは。",
        segment_type="dialogue",
        speaker_id="alice",
        scene="daily",
        emotion=Emotion.HAPPY,
        intensity=0.7,
        voice_intent=intent,
        source_ref=source,
    )

    assert utterance.segment_type is SegmentType.DIALOGUE
    assert utterance.emotion is Emotion.HAPPY
    assert utterance.speaker == "alice"
    assert utterance.voice_intent.rate == 1.05
    assert utterance.source_ref is source
    with pytest.raises((AttributeError, TypeError)):
        utterance.text = "変更"  # type: ignore[misc]


@pytest.mark.parametrize("intensity", [-0.01, 1.01, float("nan"), float("inf")])
def test_intensity_is_finite_and_between_zero_and_one(intensity: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        Utterance(id="u", order=0, text="本文", segment_type="narration", intensity=intensity)


def test_invalid_segment_type_and_emotion_are_rejected() -> None:
    with pytest.raises(ValueError):
        Utterance(id="u", order=0, text="本文", segment_type="unknown")
    with pytest.raises(ValueError):
        Utterance(id="u", order=0, text="本文", segment_type="narration", emotion="joyful")


def test_source_ref_rejects_reversed_offsets() -> None:
    with pytest.raises(ValueError, match="end"):
        SourceRef(chapter=1, start=10, end=2)


def test_warning_roundtrip_and_validation() -> None:
    warning = PlanWarning(code="NLP_FALLBACK", message="LLM unavailable", utterance_id="u-1")
    assert PlanWarning.from_dict(warning.to_dict()) == warning
    with pytest.raises(ValueError):
        PlanWarning(code="", message="detail")
