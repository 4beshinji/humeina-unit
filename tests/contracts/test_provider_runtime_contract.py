"""Runtime provider contracts for F01/F02/F10 and D04."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path, PureWindowsPath

import pytest

from yomiage.adapters.tts.legacy import VoisonaFileProviderAdapter
from yomiage.application.operations import ExternalProcessRunner, OperationDeadline
from yomiage.core.models import VoiceIntent
from yomiage.ports import tts as tts_ports
from yomiage.ports.tts import (
    MemoryTarget,
    ProviderOutputMode,
    ProviderSynthesisRequest,
    RemotePlaybackTarget,
)
from yomiage.tts.audio_utils import write_silence_wav
from yomiage.tts.shared_directory import VoisonaSharedDirectory
from yomiage.tts.voicepeak import VoicepeakProvider
from yomiage.tts.voisona import VoisonaProvider


@pytest.mark.asyncio
async def test_f01_voisona_share_is_independent_of_canonical_run_path(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "output"
    run_dir = output_root / "works" / "work-a" / "runs" / "run-a"

    shared_root = tmp_path / "shared"
    shared_root.mkdir()

    class _FileProvider:
        name = "voisona"
        destination: str | None = None
        calls = 0

        async def synthesize_to_file(self, _text: str, output_path: str, **_kwargs: object):
            self.calls += 1
            self.destination = output_path
            filename = output_path.rsplit("\\", 1)[-1]
            write_silence_wav(shared_root / filename, 0.01)
            return type("Audio", (), {"audio_data": b"", "format": "wav"})()

    provider = _FileProvider()
    result = await VoisonaFileProviderAdapter(
        provider, shared_directory=VoisonaSharedDirectory(shared_root, PureWindowsPath("Z:/"))
    ).synthesize(
        ProviderSynthesisRequest(
            text="本文",
            voice_intent=VoiceIntent(),
            destination="audio/0000.wav",
            metadata={
                "output_root": str(output_root),
                "run_dir": str(run_dir),
                "vm_mount": "Z:",
            },
        )
    )

    assert provider.calls == 1
    assert isinstance(provider.destination, str)
    assert PureWindowsPath(provider.destination).parent == PureWindowsPath("Z:/")
    assert not run_dir.exists()
    assert not list(shared_root.iterdir())
    assert result.audio_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "expected_operation", "expected_mode"),
    [
        (MemoryTarget(), "file", ProviderOutputMode.AUDIO_BYTES),
        (RemotePlaybackTarget(), "playback", ProviderOutputMode.REMOTE_PLAYBACK),
    ],
)
async def test_f01_voisona_target_uses_exactly_one_provider_operation(
    target: MemoryTarget | RemotePlaybackTarget,
    expected_operation: str,
    expected_mode: ProviderOutputMode,
    tmp_path: Path,
) -> None:
    class _Provider:
        name = "voisona"

        def __init__(self) -> None:
            self.operations: list[str] = []

        async def synthesize(self, _text: str, **_kwargs: object):
            self.operations.append("playback")
            return type("Audio", (), {"audio_data": b"", "format": "wav"})()

        async def synthesize_to_file(
            self, _text: str, output_path: str, **_kwargs: object
        ):
            self.operations.append("file")
            write_silence_wav(tmp_path / PureWindowsPath(output_path).name, 0.01)
            return type("Audio", (), {"audio_data": b"", "format": "wav"})()

    provider = _Provider()
    adapter = VoisonaFileProviderAdapter(
        provider, shared_directory=VoisonaSharedDirectory(tmp_path, PureWindowsPath("Z:/"))
    )
    result = await adapter.synthesize(
        ProviderSynthesisRequest(
            text="本文",
            voice_intent=VoiceIntent(),
            target=target,
        )
    )

    assert provider.operations == [expected_operation]
    assert result.output_mode is expected_mode
    assert bool(result.audio_data) is isinstance(target, MemoryTarget)


class _ProcessTracker:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.started: list[_FakeProcess] = []
        self.release = asyncio.Event()


class _FakeProcess:
    def __init__(self, tracker: _ProcessTracker, args: tuple[object, ...]) -> None:
        self.tracker = tracker
        self.args = args
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self.entered = asyncio.Event()
        tracker.started.append(self)

    async def communicate(self, _input: bytes | None = None):
        self.tracker.active += 1
        self.tracker.maximum = max(self.tracker.maximum, self.tracker.active)
        self.entered.set()
        try:
            await self.tracker.release.wait()
            if "-o" in self.args:
                output = Path(str(self.args[self.args.index("-o") + 1]))
                write_silence_wav(output, 0.01)
            self.returncode = 0
            return b"narrator\n", b""
        finally:
            self.tracker.active -= 1

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15
        self.tracker.release.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.tracker.release.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        return self.returncode or 0


@pytest.mark.asyncio
async def test_f02_voicepeak_health_list_and_synthesis_share_one_process_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _ProcessTracker()

    async def create_process(*args: object, **_kwargs: object) -> _FakeProcess:
        return _FakeProcess(tracker, args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    provider = VoicepeakProvider({"path": "voicepeak", "max_retries": 0})
    tasks = [
        asyncio.create_task(provider.is_available()),
        asyncio.create_task(provider.list_voices()),
        asyncio.create_task(provider.synthesize("本文")),
    ]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    tracker.release.set()
    await asyncio.gather(*tasks)

    assert tracker.maximum == 1
    await provider.close()


@pytest.mark.asyncio
async def test_external_process_lock_wait_is_bounded_by_deadline() -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    runner = ExternalProcessRunner(lock=lock)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            await runner.run(
                ["must-not-start"],
                deadline=OperationDeadline.after(0.01),
            )
    finally:
        lock.release()

    assert time.monotonic() - started < 0.05
    assert runner.active_processes == 0


@pytest.mark.asyncio
async def test_f02_voicepeak_cancel_reaps_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _ProcessTracker()

    async def create_process(*args: object, **_kwargs: object) -> _FakeProcess:
        return _FakeProcess(tracker, args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    provider = VoicepeakProvider({"path": "voicepeak", "max_retries": 0})
    task = asyncio.create_task(provider.synthesize("cancel"))
    while not tracker.started:
        await asyncio.sleep(0)
    process = tracker.started[0]
    await process.entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminate_calls + process.kill_calls >= 1
    assert process.wait_calls >= 1
    assert tracker.active == 0
    await provider.close()


class _SlowResponse:
    status = 200

    async def __aenter__(self):
        await asyncio.sleep(0.2)
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> dict[str, str]:
        return {"state": "queued"}


class _SlowSession:
    closed = False

    def get(self, *_args: object, **_kwargs: object) -> _SlowResponse:
        return _SlowResponse()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_f10_voisona_poll_uses_wall_clock_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yomiage.tts import voisona as module

    monkeypatch.setattr(module, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(module, "POLL_TIMEOUT", 0.03)
    provider = VoisonaProvider({})
    provider._session = _SlowSession()
    started = time.monotonic()

    with pytest.raises(Exception):
        await provider._poll_synthesis("job", started)

    assert time.monotonic() - started < 0.08
    await provider.close()


@pytest.mark.asyncio
async def test_voisona_poll_retries_request_timeout_before_wall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yomiage.tts import voisona as module

    class _TimeoutResponse:
        async def __aenter__(self):
            raise asyncio.TimeoutError("transient")

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _SuccessResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def json(self) -> dict[str, object]:
            return {"state": "succeeded", "duration": 0.25}

    class _TransientSession:
        closed = False

        def __init__(self) -> None:
            self.calls = 0

        def get(self, *_args: object, **_kwargs: object):
            self.calls += 1
            return _TimeoutResponse() if self.calls == 1 else _SuccessResponse()

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(module, "POLL_INTERVAL", 0.0)
    provider = VoisonaProvider({})
    session = _TransientSession()
    provider._session = session
    started = time.monotonic()

    result = await provider._poll_synthesis(
        "job",
        started,
        deadline=OperationDeadline.after(0.1),
    )

    assert session.calls == 2
    assert result.duration == pytest.approx(0.25)
    await provider.close()


def test_d04_provider_contract_declares_typed_capabilities_and_targets() -> None:
    required = {
        "ProviderCapabilities",
        "MemoryTarget",
        "ArtifactTarget",
        "SharedFileTarget",
        "RemotePlaybackTarget",
    }

    assert required <= set(vars(tts_ports))
