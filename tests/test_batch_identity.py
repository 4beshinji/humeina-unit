"""RUN-001 identity and path compatibility contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from yomiage.batch.engine import BatchEngine
from yomiage.batch.manifest import BatchManifest
from yomiage.batch.paths import BatchPaths
from yomiage.core.identity import (
    build_identity,
    legacy_url_work_id,
    new_job_id,
    new_run_id,
    plan_id_for_source,
    work_id_for_source,
)


def _manifest(url: str, *, mode: str, run_id: str) -> BatchManifest:
    identity = build_identity(url, {"analysis_window_chars": 100, "mode": mode})
    return BatchManifest(
        work_id=identity.work_id,
        work_title="Test",
        source_url=url,
        mode=mode,
        plan_id=identity.plan_id,
        run_id=run_id,
        source_fingerprint=identity.source_fingerprint,
        config_fingerprint=identity.config_fingerprint,
        analysis_complete=True,
    )


def test_plan_is_stable_when_provider_changes() -> None:
    url = "HTTPS://Example.test:443/novel/?b=2&a=1#chapter"
    assert plan_id_for_source(url, {"analysis_window_chars": 100, "mode": "voicevox"}) == (
        plan_id_for_source(url, {"mode": "voisona", "analysis_window_chars": 100})
    )
    assert work_id_for_source(url) == work_id_for_source(
        "https://example.test/novel?a=1&b=2"
    )


def test_run_and_job_ids_are_unique() -> None:
    assert len({new_run_id() for _ in range(128)}) == 128
    assert len({new_job_id() for _ in range(128)}) == 128


def test_concurrent_runs_use_distinct_directories(tmp_path: Path) -> None:
    url = "https://example.test/novel"
    run_ids = [new_run_id(), new_run_id()]

    def save(run_id: str) -> Path:
        return _manifest(url, mode="voicevox", run_id=run_id).save(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(save, run_ids))

    assert paths[0].parent != paths[1].parent
    assert all(
        path
        == BatchPaths.find_manifest(
            tmp_path,
            work_id_for_source(url),
            run_id=run_id,
        )
        for path, run_id in zip(paths, run_ids)
    )
    assert len(list((tmp_path / "works" / work_id_for_source(url) / "runs").iterdir())) == 2


def test_legacy_md5_manifest_remains_readable(tmp_path: Path) -> None:
    url = "https://example.test/novel"
    legacy_id = legacy_url_work_id(url)
    legacy_dir = tmp_path / legacy_id
    legacy_dir.mkdir()
    (legacy_dir / "manifest.json").write_text(
        json.dumps(
            {
                "work_id": legacy_id,
                "work_title": "Legacy",
                "source_url": url,
                "mode": "voicevox",
                "chapters": [],
                "characters": {},
                "sentences": [],
                "analysis_complete": True,
                "synthesis_complete": False,
            }
        )
    )

    loaded = BatchManifest.load(tmp_path, legacy_id)
    assert loaded.work_id == legacy_id
    assert loaded.run_id is None
    assert loaded.output_dir(tmp_path) == legacy_dir


@pytest.mark.asyncio
async def test_provider_change_forks_a_new_run_and_reuses_plan(tmp_path: Path) -> None:
    url = "https://example.test/novel"
    original = _manifest(url, mode="voisona", run_id=new_run_id())
    original.analysis_complete = True
    original.save(tmp_path)

    class FakeSynthesizer:
        async def is_available(self) -> bool:
            return True

    engine = BatchEngine({}, mode="voicevox", output_dir=str(tmp_path))
    engine._create_synthesizer = lambda *_args, **_kwargs: FakeSynthesizer()
    forked = await engine.synthesize(original.work_id)

    assert forked.plan_id == original.plan_id
    assert forked.run_id != original.run_id
    assert forked.mode == "voicevox"
    assert forked.output_dir(tmp_path) != original.output_dir(tmp_path)
