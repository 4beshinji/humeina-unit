"""Batch成果物の完全性とpartial終了状態の契約テスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from yomiage.batch.concatenator import Concatenator
from yomiage.batch.job_manager import BatchJobManager
from yomiage.batch.manifest import BatchManifest, ChapterMeta, SentenceEntry


def _make_manifest(
    base_dir: Path,
    statuses: tuple[str, ...] = ("synthesized", "synthesized"),
    *,
    missing_audio: bool = False,
) -> BatchManifest:
    """テスト用の1チャプターマニフェストを作成する."""
    work_dir = base_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    sentences: list[SentenceEntry] = []
    for index, status in enumerate(statuses):
        audio_file = f"{index:04d}.wav" if status == "synthesized" else None
        sentences.append(
            SentenceEntry(
                index=index,
                text=f"テスト{index}",
                chapter_index=0,
                segment_type="narration",
                status=status,
                audio_file=audio_file,
            )
        )
        if audio_file and not (missing_audio and index == 0):
            (work_dir / audio_file).write_bytes(b"wav")

    return BatchManifest(
        work_id="work",
        work_title="テスト",
        source_url="https://example.com/work",
        mode="voicevox",
        chapters=[
            ChapterMeta(
                index=0,
                title="第1章",
                url="https://example.com/work#1",
                sentence_start=0,
                sentence_end=len(sentences),
            )
        ],
        sentences=sentences,
        analysis_complete=True,
    )


def test_manifest_completion_statuses(tmp_path: Path) -> None:
    """成功、警告、部分失敗、全失敗をcompletedへ混同しない."""
    complete = _make_manifest(tmp_path)
    assert complete.compute_completion_status(tmp_path) == "complete"

    warning = _make_manifest(tmp_path / "warning", ("synthesized", "skipped"))
    assert warning.compute_completion_status(tmp_path / "warning") == "complete_with_warnings"

    partial = _make_manifest(tmp_path / "partial", ("synthesized", "failed"))
    assert partial.compute_completion_status(tmp_path / "partial") == "partial"

    failed = _make_manifest(tmp_path / "failed", ("failed", "pending"))
    assert failed.compute_completion_status(tmp_path / "failed") == "failed"


def test_missing_audio_is_not_complete(tmp_path: Path) -> None:
    """synthesized記録だけ残り、音声実体がない場合もpartialになる."""
    manifest = _make_manifest(tmp_path, missing_audio=True)
    assert manifest.compute_completion_status(tmp_path) == "partial"


def test_concat_refuses_incomplete_manifest_without_allow_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既定concatでは欠損文を飛ばしたfull.wavを作らない."""
    manifest = _make_manifest(tmp_path, ("synthesized", "failed"))
    manifest.save(tmp_path)
    monkeypatch.setattr(
        "yomiage.batch.concatenator.concat_wav_files",
        lambda _files, output, _fmt: output.write_bytes(b"full"),
    )

    result = Concatenator().concat_chapters_then_full(manifest, tmp_path)

    assert result is None
    work_dir = tmp_path / "work"
    assert not (work_dir / "full.wav").exists()
    assert not (work_dir / "full.partial.wav").exists()


def test_concat_partial_is_explicitly_named_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allow_partial時だけ、partial名のfull成果物を生成する."""
    manifest = _make_manifest(tmp_path, ("synthesized", "failed"))
    manifest.save(tmp_path)
    monkeypatch.setattr(
        "yomiage.batch.concatenator.concat_wav_files",
        lambda _files, output, _fmt: output.write_bytes(b"partial"),
    )

    result = Concatenator().concat_chapters_then_full(
        manifest, tmp_path, allow_partial=True
    )

    assert result is not None
    assert result.name == "full.partial.wav"
    assert result.exists()
    assert not (tmp_path / "work" / "full.wav").exists()
    loaded = BatchManifest.load(tmp_path, "work")
    assert loaded.completion_status == "partial"
    assert loaded.synthesis_complete is False


@pytest.mark.asyncio
async def test_job_manager_does_not_complete_partial_job_without_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APIジョブも既定ではpartial成果物をcompletedとして返さない."""
    manifest = _make_manifest(tmp_path, ("synthesized", "failed"))

    class FakeEngine:
        def __init__(self, *_args, **_kwargs):
            self.concat_called = False

        async def analyze(self, _url):
            return manifest

        async def synthesize(self, _work_id, **_kwargs):
            return manifest

        def concat(self, _work_id, **_kwargs):
            self.concat_called = True
            return tmp_path / "work" / "full.partial.wav"

    fake_engine = FakeEngine()
    monkeypatch.setattr(
        "yomiage.batch.job_manager.BatchEngine", lambda *_args, **_kwargs: fake_engine
    )

    manager = BatchJobManager({}, output_dir=str(tmp_path))
    job = manager.create_job("https://example.com/work")
    await manager.run_job(job.id)

    assert job.status == "failed"
    assert job.completion_status == "partial"
    assert job.result is not None
    assert job.result["status"] == "partial"
    assert job.output_path is None
    assert fake_engine.concat_called is False


@pytest.mark.asyncio
async def test_job_manager_exposes_partial_when_explicitly_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allow_partial時はAPI状態・成果物パスへpartialを残す."""
    manifest = _make_manifest(tmp_path, ("synthesized", "failed"))

    class FakeEngine:
        def __init__(self, *_args, **_kwargs):
            self.concat_kwargs: dict = {}

        async def analyze(self, _url):
            return manifest

        async def synthesize(self, _work_id, **_kwargs):
            return manifest

        def concat(self, _work_id, **kwargs):
            self.concat_kwargs = kwargs
            return tmp_path / "work" / "full.partial.wav"

    fake_engine = FakeEngine()
    monkeypatch.setattr(
        "yomiage.batch.job_manager.BatchEngine", lambda *_args, **_kwargs: fake_engine
    )

    manager = BatchJobManager({}, output_dir=str(tmp_path))
    job = manager.create_job("https://example.com/work", allow_partial=True)
    await manager.run_job(job.id)

    assert job.status == "partial"
    assert job.completion_status == "partial"
    assert job.result is not None
    assert job.result["status"] == "partial"
    assert job.output_path and "partial" in job.output_path
    assert fake_engine.concat_kwargs == {"allow_partial": True}
