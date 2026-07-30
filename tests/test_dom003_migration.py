"""DOM-003 canonical fingerprint と plan/schema migration の契約。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yomiage.batch.manifest import BatchManifest
from yomiage.batch.manifest_adapter import import_manifest_to_plan
from yomiage.core.identity import canonical_json, config_fingerprint, plan_id_for_source
from yomiage.core.migration import (
    MigrationPathError,
    PlanSerializer,
    SchemaMigrationRegistry,
    UnknownSchemaVersionError,
)
from yomiage.core.plan import UtterancePlan


def _plan_payload(*, schema_version: int = 1) -> dict:
    return {
        "schema_version": schema_version,
        "plan_id": "plan-test",
        "source_fingerprint": "source-test",
        "config_fingerprint": "config-test",
        "created_at": "2026-07-30T00:00:00+00:00",
        "utterances": [],
        "warnings": [],
    }


def test_canonical_json_and_plan_id_ignore_mapping_order() -> None:
    left = {
        "analysis": {"window": 200, "model": "qwen"},
        "execution": {"provider": "voisona", "format": "wav"},
    }
    right = {
        "execution": {"format": "mp3", "provider": "voicevox"},
        "analysis": {"model": "qwen", "window": 200},
    }

    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert config_fingerprint(left) == config_fingerprint(right)
    assert plan_id_for_source("https://example.test/book", left) == plan_id_for_source(
        "https://example.test/book", right
    )


def test_analysis_setting_change_changes_plan_id_but_runtime_change_does_not() -> None:
    base = {"analysis": {"window": 200, "model": "qwen"}, "execution": {"provider": "a"}}
    runtime_changed = {
        "analysis": {"window": 200, "model": "qwen"},
        "execution": {"provider": "b", "timeout": 30, "output_format": "wav"},
    }
    analysis_changed = {"analysis": {"window": 201, "model": "qwen"}}

    assert plan_id_for_source("https://example.test/book", base) == plan_id_for_source(
        "https://example.test/book", runtime_changed
    )
    assert plan_id_for_source("https://example.test/book", base) != plan_id_for_source(
        "https://example.test/book", analysis_changed
    )


def test_registry_requires_explicit_migration_and_rejects_future_version() -> None:
    registry = SchemaMigrationRegistry(2)

    @registry.register(1, 2)
    def add_field(value: dict) -> dict:
        value["migrated"] = True
        return value

    assert registry.migrate({"schema_version": 1}) == {
        "schema_version": 2,
        "migrated": True,
    }
    with pytest.raises(UnknownSchemaVersionError):
        registry.migrate({"schema_version": 3})

    no_path = SchemaMigrationRegistry(2)
    with pytest.raises(MigrationPathError):
        no_path.migrate({"schema_version": 1})


def test_plan_serializer_rejects_unknown_future_schema() -> None:
    with pytest.raises(UnknownSchemaVersionError, match="future"):
        PlanSerializer.loads(json.dumps(_plan_payload(schema_version=99)))


def test_legacy_manifest_import_is_read_only_and_round_trips_canonical_plan(
    tmp_path: Path,
) -> None:
    url = "https://example.test/book/1"
    legacy_id = "legacy-work"
    legacy_dir = tmp_path / legacy_id
    legacy_dir.mkdir()
    payload = {
        "work_id": legacy_id,
        "work_title": "Legacy",
        "source_url": url,
        "mode": "voicevox",
        "chapters": [],
        "characters": {},
        "sentences": [
            {
                "index": 1,
                "text": "Aの台詞",
                "chapter_index": 0,
                "segment_type": "dialogue",
                "speaker": "A",
            },
            {
                "index": 0,
                "text": "地の文",
                "chapter_index": 0,
                "segment_type": "narration",
            },
        ],
        "analysis_complete": True,
    }
    manifest_path = legacy_dir / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = BatchManifest.load(tmp_path, legacy_id)
    plan = import_manifest_to_plan(manifest)
    assert [(item.order, item.speaker_id) for item in plan.utterances] == [
        (0, None),
        (1, "A"),
    ]

    imported_path = manifest.save_plan(tmp_path)
    assert imported_path.is_file()
    assert json.loads(imported_path.read_text(encoding="utf-8"))["utterances"]
    assert manifest_path.read_text(encoding="utf-8") == json.dumps(payload)
    assert BatchManifest.load_plan(tmp_path, legacy_id, plan.plan_id) == UtterancePlan.from_json(
        imported_path.read_text(encoding="utf-8")
    )


def test_manifest_future_schema_is_explicitly_rejected() -> None:
    # Exercise the adapter without touching the filesystem.
    from yomiage.batch.manifest import ManifestSchemaAdapter

    with pytest.raises(ValueError, match="unsupported manifest schema_version"):
        ManifestSchemaAdapter.from_dict(
            {
                "schema_version": 999,
                "work_id": "work",
                "source_url": "https://example.test",
                "mode": "voicevox",
                "chapters": [],
                "sentences": [],
            }
        )
