"""JOB-002 FastAPI lifespanとTaskSupervisorの接続。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from yomiage import server
from yomiage.adapters.persistence.sqlite import SQLiteJobRepository
from yomiage.core.jobs import Job, JobState


class _Provider:
    def __init__(self, closed: list[str], name: str) -> None:
        self.closed = closed
        self.name = name

    async def close(self) -> None:
        self.closed.append(self.name)


class _Engine:
    def __init__(self, closed: list[str]) -> None:
        self.is_running = False
        self.tts = SimpleNamespace(
            primary=_Provider(closed, "primary"),
            fallback=_Provider(closed, "fallback"),
            stop=self.stop_tts,
        )
        self.read_started = asyncio.Event()

    async def read_url(self, url: str) -> None:
        self.read_started.set()

    async def stop_tts(self) -> None:
        self.closed_tts = True


def _config(path: Path, output: Path) -> dict:
    return {
        "storage": {"sqlite_path": str(path)},
        "server": {
            "max_concurrent_jobs": 1,
            "job_queue_size": 2,
            "shutdown_grace_period": 0,
        },
        "batch": {"output_dir": str(output)},
        "tts": {},
        "reader": {},
    }


def test_lifespan_recovers_running_job_and_closes_repository_and_tts(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "jobs.sqlite3"
    repository = SQLiteJobRepository(path)
    repository.create(Job(id="restart-me", options={"kind": "test"}))
    repository.transition(
        "restart-me",
        JobState.RUNNING,
        expected_state=JobState.PENDING,
        expected_revision=0,
    )
    repository.close()
    closed: list[str] = []
    engine = _Engine(closed)
    monkeypatch.setattr(server, "load_config", lambda: _config(path, tmp_path / "output"))
    monkeypatch.setattr(server, "_create_engine", lambda _config: engine)

    with TestClient(server.app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert server.app.state.task_supervisor.is_started is True
        recovered = server.app.state.job_repository.get("restart-me")
        assert recovered is not None
        assert recovered.state is JobState.INTERRUPTED

    reopened = SQLiteJobRepository(path)
    assert reopened.get("restart-me").state is JobState.INTERRUPTED
    reopened.close()
    assert closed == ["primary", "fallback"]
    assert server._supervisor is None
    assert server._job_repository is None


def test_read_endpoint_uses_lifespan_supervisor_task_registry(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "jobs.sqlite3"
    closed: list[str] = []
    engine = _Engine(closed)
    monkeypatch.setattr(server, "load_config", lambda: _config(path, tmp_path / "output"))
    monkeypatch.setattr(server, "_create_engine", lambda _config: engine)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/yomiage/read",
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            job = server.app.state.job_repository.get(job_id)
            if job is not None and job.state is JobState.COMPLETED:
                break
            time.sleep(0.01)
        assert server.app.state.job_repository.get(job_id).state is JobState.COMPLETED
        assert job_id not in server.app.state.task_supervisor.active_job_ids


def test_batch_failure_status_is_persisted_as_failed_job(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "jobs.sqlite3"
    closed: list[str] = []
    engine = _Engine(closed)
    monkeypatch.setattr(server, "load_config", lambda: _config(path, tmp_path / "output"))
    monkeypatch.setattr(server, "_create_engine", lambda _config: engine)

    with TestClient(server.app) as client:
        manager = server._job_manager

        async def failed_run(job_id: str, **_kwargs: object) -> None:
            manager.get_job(job_id).status = "failed"
            manager.get_job(job_id).error = "provider failed"

        monkeypatch.setattr(manager, "run_job", failed_run)
        response = client.post(
            "/api/yomiage/synthesize/batch",
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            job = server.app.state.job_repository.get(job_id)
            if job is not None and job.state is JobState.FAILED:
                break
            time.sleep(0.01)
        failed = server.app.state.job_repository.get(job_id)
        assert failed.state is JobState.FAILED
        assert failed.error_category is not None
        assert failed.error_message == "provider failed"
