"""Entrypoint matrix contracts for the shared ``SynthesisService`` policy."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from yomiage.adapters.persistence.filesystem import FilesystemArtifactStore
from yomiage.api.bridge import TTSBridge
from yomiage.application.synthesis import SynthesisFailure, SynthesisService
from yomiage.batch.engine import BatchEngine
from yomiage.batch.manifest import BatchManifest, ChapterMeta, SentenceEntry
from yomiage.core.identity import new_run_id
from yomiage.core.models import SegmentType, SourceRef, Utterance, VoiceIntent
from yomiage.core.plan import UtterancePlan
from yomiage.reader.bookmark import BookmarkManager
from yomiage.reader.engine import ReadingEngine
from yomiage.reader.playback import UtterancePlaybackQueue
from yomiage.studio.engine import StudioEngine

from .fakes import PortFakeProvider, wav_bytes


class FakeTTSManager:
    """Small manager facade needed by ``ReadingEngine``'s plan entrypoint."""

    def __init__(self, provider: PortFakeProvider) -> None:
        self.primary = provider
        self.fallback = None

    def _is_slow_provider(self) -> bool:
        return False

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None

    def stop(self) -> None:
        return None


def make_plan() -> UtterancePlan:
    return UtterancePlan(
        schema_version=1,
        plan_id="contract-plan",
        source_fingerprint="contract-source",
        config_fingerprint="contract-config",
        utterances=(
            Utterance(
                id="u-0",
                order=0,
                text="alpha",
                segment_type=SegmentType.DIALOGUE,
                source_ref=SourceRef(url="https://example.test/book", chapter=0),
                voice_intent=VoiceIntent(preferred_profile="primary"),
            ),
        ),
    )


def make_manifest(output_dir: Path, *, sentence_count: int = 1) -> BatchManifest:
    sentences = [SentenceEntry(0, "本文です。", 0, "narration")]
    if sentence_count > 1:
        sentences.append(SentenceEntry(1, "続きです。", 0, "narration"))
    manifest = BatchManifest(
        work_id="contract-work",
        work_title="contract",
        source_url="https://example.test/book",
        mode="primary",
        plan_id="contract-plan",
        run_id=new_run_id(),
        analysis_complete=True,
        chapters=[
            ChapterMeta(
                index=0,
                title="chapter",
                url="https://example.test/book#0",
                sentence_start=0,
                sentence_end=sentence_count,
            )
        ],
        sentences=sentences,
    )
    manifest.save(output_dir)
    return manifest


def service_with_fallback(*, primary_fail: bool = True, delay: float = 0.0) -> tuple[
    SynthesisService, PortFakeProvider, PortFakeProvider
]:
    primary = PortFakeProvider("primary", fail=primary_fail, delay=delay)
    fallback = PortFakeProvider("fallback", audio_data=wav_bytes())
    service = SynthesisService(
        {"primary": primary, "fallback": fallback},
        primary="primary",
        fallback="fallback",
    )
    return service, primary, fallback


def service_without_fallback(*, primary_fail: bool = True, delay: float = 0.0) -> tuple[
    SynthesisService, PortFakeProvider
]:
    primary = PortFakeProvider("primary", fail=primary_fail, delay=delay)
    return SynthesisService(primary), primary


@pytest.mark.asyncio
async def test_sdk_entrypoint_uses_shared_fallback_policy() -> None:
    service, primary, fallback = service_with_fallback()
    bridge = TTSBridge.from_service(service)

    result = await bridge.synthesize("sdk fallback")

    assert result.audio_data == wav_bytes()
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    await service.close()


@pytest.mark.asyncio
async def test_reader_entrypoint_uses_shared_fallback_policy(tmp_path: Path) -> None:
    service, primary, fallback = service_with_fallback()
    played: list[bytes] = []
    queue = UtterancePlaybackQueue(service, play_audio=played.append)
    engine = ReadingEngine(
        FakeTTSManager(primary),
        synthesis_service=service,
        bookmark_manager=BookmarkManager(tmp_path / "bookmarks"),
        playback_queue=queue,
    )

    results = await engine.read_plan(make_plan())

    assert len(results) == 1
    assert results[0].fallback_used
    assert played == [wav_bytes()]
    assert len(primary.calls) == 1 and len(fallback.calls) == 1
    await service.close()


@pytest.mark.asyncio
async def test_batch_entrypoint_uses_shared_fallback_policy(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    manifest = make_manifest(output_dir)
    service, primary, fallback = service_with_fallback()
    engine = BatchEngine(
        {},
        mode="primary",
        output_dir=str(output_dir),
        synthesis_service=service,
        artifact_store=FilesystemArtifactStore(output_dir),
    )

    result = await engine.synthesize(manifest.work_id, run_id=manifest.run_id)

    assert result.completion_status == "complete"
    assert len(primary.calls) == 1 and len(fallback.calls) == 1
    record = engine.artifact_store.load_manifest(manifest.work_id, manifest.run_id).get("0")
    assert record is not None and record.provider == "fallback"
    await service.close()


@pytest.mark.asyncio
async def test_studio_entrypoint_uses_shared_fallback_policy(tmp_path: Path) -> None:
    script = tmp_path / "script.txt"
    script.write_text("話者: studio fallback\n", encoding="utf-8")
    service, primary, fallback = service_with_fallback()
    engine = StudioEngine(
        {
            "studio": {
                "default_provider": "primary",
                "cache_enabled": False,
            }
        },
        output_dir=tmp_path / "output",
        synthesis_service=service,
    )

    project = await engine.synth(script, provider="primary", output_format="plain")

    assert len(project.results) == 1
    assert project.results[0].wav_path.read_bytes() == wav_bytes()
    assert len(primary.calls) == 1 and len(fallback.calls) == 1
    await service.close()


def test_cli_entrypoint_uses_same_service_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke the production Typer command while injecting only its composition root."""

    from typer.testing import CliRunner

    from yomiage import cli

    output_dir = tmp_path / "output"
    manifest = make_manifest(output_dir)
    service, primary, fallback = service_with_fallback()
    engine = BatchEngine(
        {},
        mode="primary",
        output_dir=str(output_dir),
        synthesis_service=service,
        artifact_store=FilesystemArtifactStore(output_dir),
    )
    monkeypatch.setattr(cli, "_load_config", lambda: {})
    monkeypatch.setattr(cli, "_create_batch_engine", lambda *_args, **_kwargs: engine)

    result = CliRunner().invoke(
        cli.app,
        [
            "batch",
            "synthesize",
            manifest.work_id,
            "--mode",
            "primary",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Synthesis complete" in result.stdout
    assert len(primary.calls) == 1 and len(fallback.calls) == 1
    asyncio.run(service.close())


@pytest.mark.asyncio
async def test_partial_failure_is_not_reported_as_success_across_entrypoints(
    tmp_path: Path,
) -> None:
    # SDK / Reader / Studio expose a failed request as an exception; Batch records the same
    # provider failure as a partial run.  None of the four paths may silently return complete.
    service, primary = service_without_fallback(primary_fail=True)
    bridge = TTSBridge.from_service(service)
    with pytest.raises(RuntimeError):
        await bridge.synthesize("sdk failure")
    await service.close()

    service, primary = service_without_fallback(primary_fail=True)
    engine = ReadingEngine(
        FakeTTSManager(primary),
        synthesis_service=service,
        bookmark_manager=BookmarkManager(tmp_path / "reader-bookmarks"),
        playback_queue=UtterancePlaybackQueue(service, play_audio=lambda _data: None),
    )
    with pytest.raises(SynthesisFailure):
        await engine.read_plan(make_plan())
    await service.close()

    output_dir = tmp_path / "batch-output"
    manifest = make_manifest(output_dir, sentence_count=2)
    service, primary = service_without_fallback(primary_fail=False)
    primary.failures = 1
    batch = BatchEngine(
        {},
        mode="primary",
        output_dir=str(output_dir),
        synthesis_service=service,
        artifact_store=FilesystemArtifactStore(output_dir),
    )
    result = await batch.synthesize(manifest.work_id, run_id=manifest.run_id)
    assert result.completion_status == "partial"

    concat_called = False
    video_called = False

    def concat(*_args: object, **_kwargs: object) -> None:
        nonlocal concat_called
        concat_called = True

    def video(*_args: object, **_kwargs: object) -> None:
        nonlocal video_called
        video_called = True

    batch.concat = concat  # type: ignore[method-assign]
    batch.video = video  # type: ignore[method-assign]

    async def fake_analyze(_url: str, *_args: object, **_kwargs: object) -> BatchManifest:
        return manifest

    async def fake_synthesize(_work_id: str, *_args: object, **_kwargs: object) -> BatchManifest:
        return result

    batch.analyze = fake_analyze  # type: ignore[method-assign]
    batch.synthesize = fake_synthesize  # type: ignore[method-assign]
    assert await batch.run("https://example.test/book", video=True) is None
    assert not concat_called and not video_called
    await service.close()

    script = tmp_path / "partial-script.txt"
    script.write_text("話者: studio failure\n", encoding="utf-8")
    service, _primary = service_without_fallback(primary_fail=True)
    studio = StudioEngine(
        {"studio": {"default_provider": "primary", "cache_enabled": False}},
        output_dir=tmp_path / "studio-output",
        synthesis_service=service,
    )
    with pytest.raises(SynthesisFailure):
        await studio.synth(script, provider="primary", output_format="plain")
    await service.close()


@pytest.mark.asyncio
async def test_cancellation_does_not_fallback_or_leave_entrypoint_tasks(tmp_path: Path) -> None:
    service, primary, fallback = service_with_fallback(primary_fail=False, delay=0.2)
    bridge = TTSBridge.from_service(service)
    sdk_task = asyncio.create_task(bridge.synthesize("sdk cancel"))
    await asyncio.sleep(0.01)
    sdk_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sdk_task
    assert fallback.calls == []
    assert service._closed is False
    await service.close()

    service, primary, fallback = service_with_fallback(primary_fail=False, delay=0.2)
    queue = UtterancePlaybackQueue(service, play_audio=lambda _data: None)
    reader = ReadingEngine(
        FakeTTSManager(primary),
        synthesis_service=service,
        bookmark_manager=BookmarkManager(tmp_path / "cancel-bookmarks"),
        playback_queue=queue,
    )
    reader_task = asyncio.create_task(reader.read_plan(make_plan()))
    await asyncio.sleep(0.01)
    reader.stop()
    await asyncio.wait_for(reader_task, timeout=1.0)
    assert fallback.calls == [] and not queue._pending_tasks
    assert service._closed is False
    await service.close()

    output_dir = tmp_path / "cancel-output"
    manifest = make_manifest(output_dir)
    service, primary, fallback = service_with_fallback(primary_fail=False, delay=0.2)
    batch = BatchEngine(
        {},
        mode="primary",
        output_dir=str(output_dir),
        synthesis_service=service,
        artifact_store=FilesystemArtifactStore(output_dir),
    )
    batch_task = asyncio.create_task(batch.synthesize(manifest.work_id, run_id=manifest.run_id))
    await asyncio.sleep(0.01)
    batch_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await batch_task
    assert fallback.calls == []
    assert service._closed is False
    await service.close()

    script = tmp_path / "cancel-script.txt"
    script.write_text(textwrap.dedent("""\
        話者: studio cancel
        """), encoding="utf-8")
    service, primary, fallback = service_with_fallback(primary_fail=False, delay=0.2)
    studio = StudioEngine(
        {"studio": {"default_provider": "primary", "cache_enabled": False}},
        output_dir=tmp_path / "studio-cancel-output",
        synthesis_service=service,
    )
    studio_task = asyncio.create_task(
        studio.synth(script, provider="primary", output_format="plain")
    )
    for _ in range(100):
        if primary.calls:
            break
        await asyncio.sleep(0.005)
    studio_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await studio_task
    assert fallback.calls == []
    assert service._closed is False
    await service.close()
