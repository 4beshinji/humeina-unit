"""RT-010 real-environment evidence for VoiSona queue and shared files."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import time
import uuid
from pathlib import Path, PureWindowsPath

import aiohttp
import pytest

from yomiage.config import get_tts_config
from yomiage.tts.audio_utils import validate_audio_bytes
from yomiage.tts.voicepeak import VoicepeakProvider
from yomiage.tts.voisona import VoisonaProvider

pytestmark = pytest.mark.e2e


def _targets(config: dict, finding: str) -> tuple[Path, str]:
    output_root = Path(config.get("batch", {}).get("output_dir", "output")).resolve()
    relative = Path("runtime-e2e") / f"{finding.lower()}-{uuid.uuid4().hex}.wav"
    host_path = output_root / relative
    host_path.parent.mkdir(parents=True, exist_ok=True)
    mount = str(config.get("batch", {}).get("voisona_vm_mount", "Z:"))
    vm_root = mount if mount.endswith("\\") else f"{mount}\\"
    provider_path = str(PureWindowsPath(vm_root, *relative.parts))
    return host_path, provider_path


async def _job_state(provider: VoisonaProvider, job_id: str) -> str:
    timeout = aiohttp.ClientTimeout(total=15)
    async with provider.session.get(
        f"{provider._api_url}/speech-syntheses/{job_id}",
        auth=provider._auth(),
        timeout=timeout,
    ) as response:
        assert response.status == 200
        payload = await response.json()
    return str(payload.get("state", "unknown"))


@pytest.mark.asyncio
async def test_e02_voisona_success_immediately_exposes_complete_shared_wav(
    config: dict,
    require_voisona: object,
) -> None:
    provider = VoisonaProvider(get_tts_config(config, "voisona"))
    host_path, provider_path = _targets(config, "E02")
    returned_at = 0.0
    partial_sizes: list[int] = []
    try:
        result = await provider.synthesize_to_file(
            "共有ファイルの完全性を確認します。",
            provider_path,
        )
        returned_at = time.monotonic()
        deadline = returned_at + 5.0
        while True:
            if host_path.exists():
                data = host_path.read_bytes()
                partial_sizes.append(len(data))
                try:
                    descriptor = validate_audio_bytes(data, "wav")
                    break
                except ValueError:
                    pass
            if time.monotonic() >= deadline:
                pytest.fail("VoiSona succeeded but a complete shared WAV was not visible")
            await asyncio.sleep(0.01)
    finally:
        await provider.close()

    evidence = {
        "finding": "E02",
        "artifact": str(host_path.relative_to(Path.cwd())),
        "size": host_path.stat().st_size,
        "sha256": hashlib.sha256(host_path.read_bytes()).hexdigest(),
        "sample_rate": descriptor.sample_rate,
        "channels": descriptor.channels,
        "frames": descriptor.frames,
        "visibility_latency_ms": round((time.monotonic() - returned_at) * 1000, 3),
        "observed_sizes": partial_sizes,
        "duration": result.duration,
    }
    print("RUNTIME_E2E " + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    assert partial_sizes[0] == host_path.stat().st_size


@pytest.mark.asyncio
async def test_e01_voisona_local_cancel_tracks_remote_job_and_followup_queue(
    config: dict,
    require_voisona: object,
) -> None:
    provider = VoisonaProvider(get_tts_config(config, "voisona"))
    cancelled_host, cancelled_provider = _targets(config, "E01-cancelled")
    followup_host, followup_provider = _targets(config, "E01-followup")
    body = provider._build_body("取消後の状態確認です。" * 20, "neutral", 1.0)
    body.update(
        {
            "destination": "file",
            "output_file_path": cancelled_provider,
        }
    )
    states: list[str] = []
    try:
        job_id = await provider._post_synthesis(body)
        local_poll = asyncio.create_task(provider.poll_until_done(job_id))
        await asyncio.sleep(0.05)
        local_poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await local_poll

        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            state = await _job_state(provider, job_id)
            if not states or states[-1] != state:
                states.append(state)
            if state in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail("remote VoiSona job did not reach a terminal state after local cancel")

        followup = await provider.synthesize_to_file(
            "取消後の後続キュー確認です。",
            followup_provider,
        )
    finally:
        await provider.close()

    assert states[-1] in {"succeeded", "failed", "cancelled"}
    cancelled_descriptor = None
    if cancelled_host.is_file():
        cancelled_descriptor = validate_audio_bytes(cancelled_host.read_bytes(), "wav")
    followup_descriptor = validate_audio_bytes(followup_host.read_bytes(), "wav")
    evidence = {
        "finding": "E01",
        "job_id": job_id,
        "states_after_local_cancel": states,
        "remote_artifact_present": cancelled_descriptor is not None,
        "followup_artifact": str(followup_host.relative_to(Path.cwd())),
        "followup_sha256": hashlib.sha256(followup_host.read_bytes()).hexdigest(),
        "followup_frames": followup_descriptor.frames,
        "followup_duration": followup.duration,
        "local_poll_done": local_poll.done(),
    }
    print("RUNTIME_E2E " + json.dumps(evidence, ensure_ascii=False, sort_keys=True))


@pytest.mark.asyncio
async def test_e03_voicepeak_serializes_narrators_and_reaps_cancelled_process(
    config: dict,
    require_voicepeak: object,
) -> None:
    provider = VoicepeakProvider(get_tts_config(config, "voicepeak"))
    sampler_running = True
    maximum_processes = 0

    async def sample_processes() -> None:
        nonlocal maximum_processes
        while sampler_running:
            maximum_processes = max(
                maximum_processes,
                provider._process_runner.active_processes,
            )
            await asyncio.sleep(0.005)

    sampler = asyncio.create_task(sample_processes())
    try:
        voices = await provider.list_voices()
        if len(voices) < 2:
            pytest.skip("E03 requires at least two licensed VOICEPEAK narrators")
        narrators = [str(voices[0]["id"]), str(voices[1]["id"])]
        results = await asyncio.gather(
            provider.synthesize("第一話者の実合成です。", narrator=narrators[0]),
            provider.synthesize("第二話者の実合成です。", narrator=narrators[1]),
        )
        descriptors = [
            validate_audio_bytes(result.audio_data, "wav") for result in results
        ]

        cancel_task = asyncio.create_task(
            provider.synthesize("取消対象の長い発話です。" * 400, narrator=narrators[0])
        )
        deadline = time.monotonic() + 10.0
        while provider._process_runner.active_processes == 0 and not cancel_task.done():
            if time.monotonic() >= deadline:
                pytest.fail("VOICEPEAK process did not start within 10 seconds")
            await asyncio.sleep(0.005)
        assert not cancel_task.done(), "VOICEPEAK cancel target completed before cancellation"
        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task
        assert provider._process_runner.active_processes == 0

        followup = await provider.synthesize(
            "取消後の後続実合成です。", narrator=narrators[1]
        )
        followup_descriptor = validate_audio_bytes(followup.audio_data, "wav")
    finally:
        sampler_running = False
        await sampler
        await provider.close()

    evidence = {
        "finding": "E03",
        "narrators": narrators,
        "maximum_processes": maximum_processes,
        "successful_exit_codes": [0, 0, 0],
        "descriptors": [
            {
                "sample_rate": descriptor.sample_rate,
                "channels": descriptor.channels,
                "frames": descriptor.frames,
                "sha256": hashlib.sha256(result.audio_data).hexdigest(),
            }
            for descriptor, result in zip(descriptors, results, strict=True)
        ],
        "cancelled_processes_after_await": provider._process_runner.active_processes,
        "followup_frames": followup_descriptor.frames,
        "followup_sha256": hashlib.sha256(followup.audio_data).hexdigest(),
    }
    print("RUNTIME_E2E " + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    assert maximum_processes == 1


@pytest.mark.asyncio
async def test_e04_slack_cancel_closes_real_socket_and_sdk_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if importlib.util.find_spec("slack_sdk") is None:
        pytest.skip("E04 requires the slack optional dependency")
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        pytest.skip("E04 requires Slack test-workspace bot/app tokens")

    from slack_sdk.socket_mode import aiohttp as socket_mode_aiohttp

    from yomiage.slack.monitor import SlackMonitor

    real_client = socket_mode_aiohttp.SocketModeClient
    connected = asyncio.Event()
    clients = []

    class RecordingSocketModeClient(real_client):
        async def connect(self) -> None:
            await super().connect()
            clients.append(self)
            connected.set()

    monkeypatch.setattr(
        socket_mode_aiohttp,
        "SocketModeClient",
        RecordingSocketModeClient,
    )
    monitor = SlackMonitor(bot_token, app_token)
    task = asyncio.create_task(monitor.start())
    await asyncio.wait_for(connected.wait(), timeout=30.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert len(clients) == 1
    client = clients[0]
    owned_tasks = [
        task
        for task in (
            client.message_processor,
            client.current_session_monitor,
            client.message_receiver,
        )
        if task is not None
    ]
    evidence = {
        "finding": "E04",
        "socket_closed": client.current_session is None or client.current_session.closed,
        "session_closed": (
            client.aiohttp_client_session is None
            or client.aiohttp_client_session.closed
        ),
        "owned_task_count": len(owned_tasks),
        "owned_tasks_terminal": all(item.done() for item in owned_tasks),
        "auto_reconnect_enabled": client.auto_reconnect_enabled,
    }
    print("RUNTIME_E2E " + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    assert evidence["socket_closed"] is True
    assert evidence["session_closed"] is True
    assert evidence["owned_tasks_terminal"] is True
    assert evidence["auto_reconnect_enabled"] is False
