"""DOM-001 UtterancePlan contract tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yomiage.core.models import PlanWarning, Utterance
from yomiage.core.plan import PlanValidationError, UtterancePlan


def _utterance(identifier: str, order: int) -> Utterance:
    return Utterance(
        id=identifier,
        order=order,
        text=f"本文{order}",
        segment_type="narration",
        source_ref={"chapter": 1, "start": order, "end": order + 1},
    )


def test_plan_dict_and_json_roundtrip() -> None:
    created_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    plan = UtterancePlan(
        schema_version=1,
        plan_id="plan-1",
        source_fingerprint="source-hash",
        config_fingerprint="config-hash",
        created_at=created_at,
        utterances=[_utterance("u-0", 0), _utterance("u-1", 1)],
        warnings=[PlanWarning("NLP_FALLBACK", "rule based analysis")],
    )

    assert UtterancePlan.from_dict(plan.to_dict()) == plan
    assert UtterancePlan.from_json(plan.to_json()) == plan


def test_plan_rejects_missing_schema_version() -> None:
    payload = {
        "plan_id": "plan-1",
        "source_fingerprint": "source-hash",
        "config_fingerprint": "config-hash",
        "created_at": "2026-07-30T12:00:00Z",
        "utterances": [],
        "warnings": [],
    }
    with pytest.raises(ValueError, match="schema_version"):
        UtterancePlan.from_dict(payload)


def test_plan_rejects_duplicate_or_out_of_order_utterances() -> None:
    with pytest.raises(PlanValidationError):
        UtterancePlan(
            schema_version=1,
            plan_id="plan-1",
            source_fingerprint="source-hash",
            config_fingerprint="config-hash",
            utterances=[_utterance("u-0", 0), _utterance("u-1", 0)],
        )
    with pytest.raises(PlanValidationError):
        UtterancePlan(
            schema_version=1,
            plan_id="plan-1",
            source_fingerprint="source-hash",
            config_fingerprint="config-hash",
            utterances=[_utterance("u-1", 1), _utterance("u-0", 0)],
        )


def test_plan_is_immutable_and_updates_explicitly() -> None:
    plan = UtterancePlan(
        schema_version=1,
        plan_id="plan-1",
        source_fingerprint="source-hash",
        config_fingerprint="config-hash",
        utterances=[_utterance("u-0", 0)],
    )
    with pytest.raises((AttributeError, TypeError)):
        plan.utterances = ()  # type: ignore[misc]

    changed = plan.with_warning(PlanWarning("INFO", "additional context", severity="info"))
    assert changed is not plan
    assert len(plan.warnings) == 0
    assert len(changed.warnings) == 1
