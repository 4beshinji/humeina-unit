"""JOB-002 FastAPI lifespanとTaskSupervisorの接続。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from yomiage.adapters.persistence.sqlite import SQLiteJobRepository
from yomiage.bootstrap import create_synthesis_service
from yomiage.core.jobs import Job, JobState
from yomiage.ports.sources import URLPolicy
from yomiage.server import ServerDependencies, _close_engine_resources, create_app
from yomiage.tts.manager import TTSManager


class _PublicResolver:
    async def resolve(self, _hostname: str) -> tuple[str, ...]:
        return ("93.184.216.34",)


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


class _ManagedProvider:
    name = "managed"
    is_slow = False

    def __init__(self) -> None:
        self.closed = 0

    async def health(self):
        return True

    async def synthesize(self, _request):
        return b"RIFF"

    async def close(self) -> None:
        self.closed += 1


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


def test_lifespan_recovers_running_job_and_closes_repository_and_tts(tmp_path: Path) -> None:
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
    application = create_app(
        _config(path, tmp_path / "output"),
        ServerDependencies(engine=engine, url_policy=URLPolicy(resolver=_PublicResolver())),
    )

    with TestClient(application) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert application.state.task_supervisor.is_started is True
        recovered = application.state.job_repository.get("restart-me")
        assert recovered is not None
        assert recovered.state is JobState.INTERRUPTED

    reopened = SQLiteJobRepository(path)
    assert reopened.get("restart-me").state is JobState.INTERRUPTED
    reopened.close()
    assert closed == ["primary", "fallback"]
    assert application.state.task_supervisor is None
    assert application.state.job_repository is None


def test_owned_canonical_service_closes_provider_exactly_once() -> None:
    provider = _ManagedProvider()
    service = create_synthesis_service({"managed": provider}, primary="managed")
    manager = TTSManager(
        provider,  # type: ignore[arg-type]
        service=service,
        owns_service=True,
    )
    engine = SimpleNamespace(is_running=False, tts=manager)

    asyncio.run(_close_engine_resources(engine))

    assert provider.closed == 1


def test_read_endpoint_uses_lifespan_supervisor_task_registry(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    closed: list[str] = []
    engine = _Engine(closed)
    application = create_app(
        _config(path, tmp_path / "output"),
        ServerDependencies(engine=engine, url_policy=URLPolicy(resolver=_PublicResolver())),
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/read",
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            job = application.state.job_repository.get(job_id)
            if job is not None and job.state is JobState.COMPLETED:
                break
            time.sleep(0.01)
        assert application.state.job_repository.get(job_id).state is JobState.COMPLETED
        assert job_id not in application.state.task_supervisor.active_job_ids


def test_batch_failure_status_is_persisted_as_failed_job(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "jobs.sqlite3"
    closed: list[str] = []
    engine = _Engine(closed)
    application = create_app(
        _config(path, tmp_path / "output"),
        ServerDependencies(engine=engine, url_policy=URLPolicy(resolver=_PublicResolver())),
    )

    with TestClient(application) as client:
        manager = application.state.job_manager

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
            job = application.state.job_repository.get(job_id)
            if job is not None and job.state is JobState.FAILED:
                break
            time.sleep(0.01)
        failed = application.state.job_repository.get(job_id)
        assert failed.state is JobState.FAILED
        assert failed.error_category is not None
        assert failed.error_message == "provider failed"
