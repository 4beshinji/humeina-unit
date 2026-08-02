"""SYN-004 BatchEngine/SynthesisService vertical-slice contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yomiage.adapters.persistence.filesystem import FilesystemArtifactStore
from yomiage.adapters.tts import VoisonaFileProviderAdapter
from yomiage.application.synthesis import SynthesisService
from yomiage.batch.engine import BatchEngine
from yomiage.batch.manifest import BatchManifest, ChapterMeta, SentenceEntry
from yomiage.core.identity import new_run_id
from yomiage.core.models import VoiceIntent
from yomiage.ports.tts import (
    ProviderError,
    ProviderErrorCategory,
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
    ProviderVoice,
)
from yomiage.tts.audio_utils import write_silence_wav


class FakeProvider:
    """Provider port fake shared by the three Batch modes."""

    def __init__(self, name: str, *, fail_ids: set[str] | None = None) -> None:
        self.name = name
        self.fail_ids = set(fail_ids or ())
        self.calls: list[ProviderSynthesisRequest] = []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(True, provider=self.name, status="healthy")

    async def list_voices(self) -> list[ProviderVoice]:
        return [ProviderVoice(f"{self.name}-voice", self.name, self.name)]

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        self.calls.append(request)
        if request.utterance_id in self.fail_ids:
            raise ProviderError(
                "planned failure",
                category=ProviderErrorCategory.SYNTHESIS,
                provider=self.name,
                retryable=False,
            )
        # Use valid WAV bytes so the real ArtifactStore validation is exercised.
        import tempfile

        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temporary = Path(handle.name)
        handle.close()
        try:
            write_silence_wav(temporary, 0.01)
            return ProviderSynthesisResult(
                audio_data=temporary.read_bytes(),
                format="wav",
                duration=0.01,
            )
        finally:
            temporary.unlink(missing_ok=True)

    async def close(self) -> None:
        return None


def _manifest(output_dir: Path, mode: str) -> BatchManifest:
    run_id = new_run_id()
    value = BatchManifest(
        work_id=f"work-{mode}",
        work_title="fake",
        source_url="https://example.test/fake",
        mode=mode,
        plan_id="plan-fake",
        run_id=run_id,
        analysis_complete=True,
        chapters=[
            ChapterMeta(
                index=0,
                title="chapter",
                url="https://example.test/fake#1",
                sentence_start=0,
                sentence_end=2,
            )
        ],
        sentences=[
            SentenceEntry(0, "これは一文です。", 0, "narration"),
            SentenceEntry(1, "これは二文目です。", 0, "dialogue", speaker="A"),
        ],
    )
    value.save(output_dir)
    return value


@pytest.mark.parametrize("mode", ("voisona", "voicevox", "voicepeak"))
@pytest.mark.asyncio
async def test_all_batch_providers_use_one_service_and_resume_without_regeneration(
    tmp_path: Path, mode: str
) -> None:
    output_dir = tmp_path / "output"
    manifest = _manifest(output_dir, mode)
    provider = FakeProvider(mode)
    store = FilesystemArtifactStore(output_dir)
    service = SynthesisService(provider, artifact_store=store)
    engine = BatchEngine(
        {},
        mode=mode,
        output_dir=str(output_dir),
        synthesis_service=service,
    )

    completed = await engine.synthesize(manifest.work_id, run_id=manifest.run_id)
    assert completed.completion_status == "complete"
    assert [request.utterance_id for request in provider.calls] == ["0", "1"]

    # A second resume is artifact-manifest driven; successful files are not sent to the fake
    # provider again.
    resumed = await engine.synthesize(manifest.work_id, run_id=manifest.run_id)
    assert resumed.completion_status == "complete"
    assert len(provider.calls) == 2
    artifact_manifest = store.load_manifest(manifest.work_id, manifest.run_id)
    assert [record.utterance_id for record in artifact_manifest.final_artifacts] == ["0", "1"]


@pytest.mark.asyncio
async def test_failed_utterance_retry_preserves_successful_artifact(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    manifest = _manifest(output_dir, "voicevox")
    provider = FakeProvider("voicevox", fail_ids={"1"})
    store = FilesystemArtifactStore(output_dir)
    service = SynthesisService(provider, artifact_store=store)
    engine = BatchEngine(
        {},
        mode="voicevox",
        output_dir=str(output_dir),
        synthesis_service=service,
    )

    partial = await engine.synthesize(manifest.work_id, run_id=manifest.run_id)
    assert partial.completion_status == "partial"
    assert [request.utterance_id for request in provider.calls] == ["0", "1"]
    first_checksum = store.load_manifest(manifest.work_id, manifest.run_id).get("0").checksum

    provider.fail_ids.clear()
    complete = await engine.synthesize(
        manifest.work_id,
        run_id=manifest.run_id,
        retry_failed=True,
    )
    assert complete.completion_status == "complete"
    assert [request.utterance_id for request in provider.calls] == ["0", "1", "1"]
    assert (
        store.load_manifest(manifest.work_id, manifest.run_id).get("0").checksum
        == first_checksum
    )


@pytest.mark.asyncio
async def test_non_speakable_sentence_is_persisted_as_skip_on_resume(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    manifest = _manifest(output_dir, "voicevox")
    manifest.sentences[1].text = "―――"
    manifest.save(output_dir)
    provider = FakeProvider("voicevox")
    store = FilesystemArtifactStore(output_dir)
    service = SynthesisService(provider, artifact_store=store)
    engine = BatchEngine(
        {},
        mode="voicevox",
        output_dir=str(output_dir),
        synthesis_service=service,
    )

    first = await engine.synthesize(manifest.work_id, run_id=manifest.run_id)
    assert first.completion_status == "complete_with_warnings"
    assert first.sentences[1].status == "skipped"
    assert [request.utterance_id for request in provider.calls] == ["0"]

    second = await engine.synthesize(manifest.work_id, run_id=manifest.run_id)
    assert second.sentences[1].status == "skipped"
    assert len(provider.calls) == 1
    assert store.load_manifest(manifest.work_id, manifest.run_id).get("1").status == "skipped"


@pytest.mark.asyncio
async def test_owned_service_closes_when_plan_execution_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "output"
    manifest = _manifest(output_dir, "voicevox")

    class ClosableProvider:
        name = "voicevox"

        def __init__(self) -> None:
            self.close_calls = 0

        async def health(self):
            return ProviderHealth(True, provider=self.name)

        async def list_voices(self):
            return [ProviderVoice("voice", "voice", self.name)]

        async def synthesize(self, _request):
            raise AssertionError("plan failure should happen before provider synthesis")

        async def close(self):
            self.close_calls += 1

    provider = ClosableProvider()
    fake_synthesizer = type("Synth", (), {"provider": provider})()
    engine = BatchEngine({}, mode="voicevox", output_dir=str(output_dir))
    monkeypatch.setattr(engine, "_create_synthesizer", lambda _db: fake_synthesizer)

    def raise_plan(*_args, **_kwargs):
        raise RuntimeError("invalid plan")

    monkeypatch.setattr(BatchManifest, "to_utterance_plan", raise_plan)
    with pytest.raises(RuntimeError, match="invalid plan"):
        await engine.synthesize(manifest.work_id, run_id=manifest.run_id)
    assert provider.close_calls == 1


@pytest.mark.asyncio
async def test_incomplete_run_does_not_concat_or_render_video(tmp_path: Path, monkeypatch) -> None:
    output_dir = tmp_path / "output"
    manifest = _manifest(output_dir, "voicevox")
    provider = FakeProvider("voicevox", fail_ids={"1"})
    service = SynthesisService(provider, artifact_store=FilesystemArtifactStore(output_dir))
    engine = BatchEngine(
        {},
        mode="voicevox",
        output_dir=str(output_dir),
        synthesis_service=service,
    )

    concat_called = False
    video_called = False

    def concat(*_args, **_kwargs):
        nonlocal concat_called
        concat_called = True
        return output_dir / "unexpected.wav"

    def video(*_args, **_kwargs):
        nonlocal video_called
        video_called = True
        return output_dir / "unexpected.mp4"

    monkeypatch.setattr(engine, "concat", concat)
    monkeypatch.setattr(engine, "video", video)
    monkeypatch.setattr(engine, "analyze", lambda *_args, **_kwargs: _async_manifest(manifest))

    result = await engine.run("https://example.test/fake", video=True)
    assert result is None
    assert concat_called is False
    assert video_called is False


@pytest.mark.asyncio
async def test_voisona_file_adapter_maps_vm_destination_to_run_artifact(tmp_path: Path) -> None:
    class FileProvider:
        name = "voisona"

        def __init__(self) -> None:
            self.destination: str | None = None

        async def synthesize_to_file(self, _text, output_path, **_kwargs):
            self.destination = str(output_path)
            # The VM fake exposes the same shared directory under the basename of the run.
            filename = str(output_path).rsplit("\\", 1)[-1]
            run_dir = tmp_path / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            write_silence_wav(run_dir / filename, 0.01)
            return type("Audio", (), {"audio_data": b"", "format": "wav"})()

    provider = FileProvider()
    adapter = VoisonaFileProviderAdapter(provider)
    result = await adapter.synthesize(
        ProviderSynthesisRequest(
            text="本文",
            voice_intent=VoiceIntent(),
            utterance_id="0",
            destination="0000.wav",
            metadata={"run_dir": str(tmp_path / "run"), "vm_mount": "Z:"},
        )
    )
    assert provider.destination is not None
    assert provider.destination.startswith("Z:\\run\\.")
    assert result.audio_data


async def _async_manifest(value: BatchManifest) -> BatchManifest:
    await asyncio.sleep(0)
    return value
