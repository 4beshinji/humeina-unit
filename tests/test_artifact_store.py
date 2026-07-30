"""RUN-002 filesystem ArtifactStore integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yomiage.adapters.persistence.filesystem import FilesystemArtifactStore
from yomiage.ports.artifacts import ArtifactValidationError
from yomiage.tts.audio_utils import write_silence_wav


def test_publish_wav_and_manifest_round_trip(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    run_dir = store.run_dir("work-1", "run-1")

    source = run_dir / "audio" / "0001.wav"
    write_silence_wav(source, 0.1)
    artifact = store.publish(
        source,
        "audio/0001.wav",
        work_id="work-1",
        run_id="run-1",
        provider="voicevox",
    )
    store.record_utterance(
        "work-1",
        "run-1",
        "0",
        status="synthesized",
        artifact=artifact,
    )

    manifest_path = store.artifact_manifest_path("work-1", "run-1")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = data["artifacts"][0]
    assert record["status"] == "synthesized"
    assert record["provider"] == "voicevox"
    assert record["checksum"] == store.checksum(run_dir / "audio" / "0001.wav")
    assert record["duration"] == pytest.approx(0.1)
    assert store.verify_manifest("work-1", "run-1") == ()


def test_invalid_wav_is_not_published_or_listed(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    run_dir = store.run_dir("work-1", "run-1")
    source = run_dir / "audio" / "bad.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not-a-wav")

    with pytest.raises(ArtifactValidationError):
        store.publish(
            source,
            "audio/bad.wav",
            work_id="work-1",
            run_id="run-1",
        )

    manifest = store.initialize_manifest("work-1", "run-1", ["0"])
    assert manifest.final_artifacts == ()
    assert json.loads(
        store.artifact_manifest_path("work-1", "run-1").read_text(encoding="utf-8")
    )["artifacts"][0]["path"] is None


def test_manifest_detects_missing_and_tampered_artifacts(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    run_dir = store.run_dir("work-1", "run-1")
    source = run_dir / "audio" / "0001.wav"
    write_silence_wav(source, 0.1)
    artifact = store.publish(
        source,
        "audio/0001.wav",
        work_id="work-1",
        run_id="run-1",
    )
    store.record_utterance("work-1", "run-1", "0", status="synthesized", artifact=artifact)

    source.write_bytes(b"changed")
    issues = store.verify_manifest("work-1", "run-1")
    assert issues and issues[0].reason == "checksum mismatch"

    source.unlink()
    issues = store.verify_manifest("work-1", "run-1")
    assert issues and issues[0].reason == "missing artifact"


def test_temporary_artifact_is_cleaned_after_context(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    with store.temporary_artifact("work-1", "run-1", "audio/0001.wav") as temporary:
        assert temporary.parent == store.run_dir("work-1", "run-1") / "audio"
        temporary.write_bytes(b"partial")
        assert temporary.is_file()
    assert not temporary.exists()
