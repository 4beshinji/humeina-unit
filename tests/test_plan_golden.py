"""TST-001 UtterancePlan golden/contract tests.

The fixtures describe the semantic plan rather than an audio provider request.  All three
entrypoints therefore have to preserve the same segment boundaries, speaker IDs, and voice
intent values before any provider-specific text splitting takes place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yomiage.api.analyzer import TextAnalyzer
from yomiage.application.planning import (
    BatchPlanningAdapter,
    ReaderPlanningAdapter,
    UtterancePlanner,
)
from yomiage.batch.manifest import BatchManifest, SentenceEntry
from yomiage.nlp.pipeline import NLPAnalyzer

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "plans"
FIXTURE_NAMES = (
    "mixed_scene_boundaries.json",
    "long_dialogue_boundaries.json",
)


def _load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def _plan_without_timestamp(plan: Any) -> dict[str, Any]:
    """Drop only ``created_at``; all semantic and identity fields remain golden-tested."""

    payload = plan.to_dict()
    payload.pop("created_at")
    return payload


def _manifest(fixture: dict[str, Any]) -> BatchManifest:
    payload = fixture["batch_manifest"]
    sentences = [SentenceEntry(**entry) for entry in payload["sentences"]]
    return BatchManifest(
        work_id=payload["work_id"],
        work_title=payload["work_title"],
        source_url=payload["source_url"],
        mode=payload["mode"],
        sentences=sentences,
    )


async def _plans_for_fixture(fixture: dict[str, Any]) -> list[Any]:
    """Build plans through Reader, public API, and legacy Batch adapters.

    A shared rule-based planner makes the fallback policy deterministic while exercising the
    same planner boundary used by all entrypoints.  No LLM/backend is contacted.
    """

    planner = UtterancePlanner(
        NLPAnalyzer.rule_based(max_chunk_chars=fixture["max_chunk_chars"])
    )
    reader_plan = await ReaderPlanningAdapter(planner).plan(
        fixture["text"],
        source=fixture["source"],
        config=fixture["config"],
    )
    api_plan = await TextAnalyzer.rule_based(
        max_chunk_chars=fixture["max_chunk_chars"]
    ).plan(
        fixture["text"],
        source=fixture["source"],
        config=fixture["config"],
    )
    batch_plan = BatchPlanningAdapter(planner).plan(
        _manifest(fixture),
        config=fixture["config"],
    )
    return [reader_plan, api_plan, batch_plan]


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
async def test_reader_api_batch_match_plan_golden(fixture_name: str) -> None:
    """Every entrypoint emits the same provider-independent plan contract."""

    fixture = _load_fixture(fixture_name)
    plans = await _plans_for_fixture(fixture)
    expected = fixture["expected_plan_v2"]
    assert [_plan_without_timestamp(plan) for plan in plans[:2]] == [expected, expected]
    # 旧manifestに失われた原文位置を補完しない。旧fixtureの境界と読み取り互換を別検証。
    legacy = _plan_without_timestamp(plans[2])
    legacy.pop("source_documents")
    for utterance in legacy["utterances"]:
        for key in ("text_content", "speaker_origin", "unresolved_reason"):
            utterance.pop(key)
    expected_legacy = {**fixture["expected_plan"], "schema_version": 2}
    expected_legacy["warnings"] = [
        {**warning, "message": "rule-based values were used without LLM scene analysis"}
        if warning["code"] == "NLP_FALLBACK" else warning
        for warning in expected_legacy["warnings"]
    ]
    assert legacy == expected_legacy


async def test_mixed_golden_requires_boundaries_not_dominant_segment() -> None:
    """A dominant-segment regression must fail on type/speaker order and scene-break warning."""

    fixture = _load_fixture("mixed_scene_boundaries.json")
    plan = (await _plans_for_fixture(fixture))[0]
    utterances = plan.to_dict()["utterances"]

    assert [item["segment_type"] for item in utterances] == [
        "narration", "narration", "dialogue", "narration", "thought",
        "narration", "dialogue", "narration", "narration",
    ]
    assert [item["speaker_id"] for item in utterances] == [
        None, None, "太郎", None, None, None, "花子", None, None,
    ]
    assert any(
        warning.code == "SCENE_BREAK_SKIPPED" for warning in plan.warnings
    )


async def test_long_golden_keeps_dialogue_whole_before_tts_split() -> None:
    """Logical planning must retain the full long dialogue and its speaker boundary."""

    fixture = _load_fixture("long_dialogue_boundaries.json")
    plans = await _plans_for_fixture(fixture)
    for plan in plans:
        dialogue = [
            item
            for item in plan.utterances
            if item.segment_type.value == "dialogue"
        ]
        assert len(dialogue) == 1
        assert dialogue[0].speaker_id == "太郎"
        assert len(dialogue[0].text) > 200
        assert dialogue[0].text == fixture["expected_plan"]["utterances"][1]["text"]
