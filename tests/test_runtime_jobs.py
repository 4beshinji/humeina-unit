"""F03/F11/F14 の job/process ownership 回帰テスト。"""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from yomiage.batch.job_manager import BatchJobManager
from yomiage.llm import LLMError, LLMErrorCategory, LLMRequest, LLMResponse, LLMUsage
from yomiage.llm.service import LLMService


class _Manifest:
    work_id = "work"
    work_title = "work"
    chapters: list = []
    total_count = 1
    plan_id = "plan"
    run_id = "run"
    job_id = None

    def save(self, _path) -> None:
        return None


@pytest.mark.asyncio
async def test_f03_batch_blocking_concat_does_not_stop_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Engine:
        async def analyze(self, _url: str) -> _Manifest:
            return _Manifest()

        async def synthesize(self, _work_id: str, **_kwargs: object) -> _Manifest:
            return _Manifest()

        def concat(self, _work_id: str, **_kwargs: object) -> str:
            time.sleep(0.08)
            return "full.wav"

        async def close(self) -> None:
            return None

    monkeypatch.setattr("yomiage.batch.job_manager.BatchEngine", lambda *_a, **_k: _Engine())
    monkeypatch.setattr(
        "yomiage.batch.job_manager._manifest_completion_status", lambda *_a: "complete"
    )
    manager = BatchJobManager({})
    job = manager.create_job("https://example.test")
    ticks = 0
    running = True

    async def heartbeat() -> None:
        nonlocal ticks
        while running:
            ticks += 1
            await asyncio.sleep(0.005)

    pulse = asyncio.create_task(heartbeat())
    try:
        await manager.run_job(job.id)
    finally:
        running = False
        await pulse

    assert ticks >= 5


@pytest.mark.asyncio
async def test_f03_batch_cancel_reaps_ffmpeg_and_joins_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    released = threading.Event()
    worker_finished = threading.Event()
    processes: list[_BlockingProcess] = []

    class _BlockingProcess:
        def __init__(self, _command: list[str], **_kwargs: object) -> None:
            self.returncode: int | None = None
            self.terminate_calls = 0
            self.kill_calls = 0
            self.communicate_calls = 0
            processes.append(self)

        def communicate(self, timeout: float | None = None):
            self.communicate_calls += 1
            started.set()
            if not released.wait(timeout):
                raise subprocess.TimeoutExpired(["ffmpeg"], timeout)
            worker_finished.set()
            return "", ""

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminate_calls += 1
            self.returncode = -15
            released.set()

        def kill(self) -> None:
            self.kill_calls += 1
            self.returncode = -9
            released.set()

    class _Engine:
        process_runner = None

        async def analyze(self, _url: str) -> _Manifest:
            return _Manifest()

        async def synthesize(self, _work_id: str, **_kwargs: object) -> _Manifest:
            return _Manifest()

        def concat(self, _work_id: str, *, process_runner, **_kwargs: object) -> str:
            self.process_runner = process_runner
            self.process_runner.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return "full.wav"

        async def close(self) -> None:
            return None

    engine = _Engine()
    monkeypatch.setattr("yomiage.application.operations.subprocess.Popen", _BlockingProcess)
    monkeypatch.setattr("yomiage.batch.job_manager.BatchEngine", lambda *_a, **_k: engine)
    monkeypatch.setattr(
        "yomiage.batch.job_manager._manifest_completion_status", lambda *_a: "complete"
    )
    manager = BatchJobManager({})
    job = manager.create_job("https://example.test")
    task = asyncio.create_task(manager.run_job(job.id))
    assert await asyncio.to_thread(started.wait, 1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert worker_finished.is_set()
    assert processes[0].terminate_calls + processes[0].kill_calls == 1
    assert engine.process_runner.active_processes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["success", "partial", "exception", "cancel"])
async def test_f11_batch_job_closes_owned_engine_once_on_every_terminal_path(
    monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    engines: list[object] = []

    class _Engine:
        def __init__(self) -> None:
            self.close_calls = 0
            self.entered = asyncio.Event()
            engines.append(self)

        async def analyze(self, _url: str) -> _Manifest:
            if terminal == "exception":
                raise RuntimeError("boom")
            if terminal == "cancel":
                self.entered.set()
                await asyncio.Event().wait()
            return _Manifest()

        async def synthesize(self, _work_id: str, **_kwargs: object) -> _Manifest:
            return _Manifest()

        def concat(self, _work_id: str, **_kwargs: object) -> str:
            return "full.wav"

        async def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr("yomiage.batch.job_manager.BatchEngine", lambda *_a, **_k: _Engine())
    monkeypatch.setattr(
        "yomiage.batch.job_manager._manifest_completion_status",
        lambda *_a: "partial" if terminal == "partial" else "complete",
    )
    manager = BatchJobManager({})
    job = manager.create_job("https://example.test")

    if terminal == "cancel":
        task = asyncio.create_task(manager.run_job(job.id))
        while not engines:
            await asyncio.sleep(0)
        await engines[0].entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await manager.run_job(job.id)

    assert engines[0].close_calls == 1


class _Transport:
    provider = "fake"
    model = "fake"
    protocol = SimpleNamespace(value="chat_completions")

    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    async def complete(self, _request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise LLMError(LLMErrorCategory.TIMEOUT, "timeout")
        return LLMResponse(
            text="ok",
            provider=self.provider,
            model=self.model,
            protocol="chat_completions",
            usage=LLMUsage(),
        )

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_f14_llm_budget_is_job_local() -> None:
    transport = _Transport()
    service = LLMService({"fake": transport}, max_requests_per_job=1)

    async with service.job_scope("job-a"):
        await service.complete(LLMRequest(task="a", prompt="one"))
        with pytest.raises(LLMError) as exhausted:
            await service.complete(LLMRequest(task="a", prompt="two"))
        assert exhausted.value.category is LLMErrorCategory.BUDGET
    async with service.job_scope("job-b"):
        response = await service.complete(LLMRequest(task="b", prompt="one"))

    assert response.text == "ok"
    await service.close()


@pytest.mark.asyncio
async def test_f14_retry_never_exceeds_physical_request_budget() -> None:
    transport = _Transport(failures=1)
    service = LLMService(
        {"fake": transport}, max_requests_per_job=1, max_retries=2
    )

    with pytest.raises(LLMError) as caught:
        async with service.job_scope("job"):
            await service.complete(LLMRequest(task="a", prompt="one"))

    assert caught.value.category is LLMErrorCategory.BUDGET
    assert transport.calls == 1
    await service.close()
