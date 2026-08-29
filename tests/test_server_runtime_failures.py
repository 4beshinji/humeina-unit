"""F09/F13 HTTP validation, admission, and error mapping contracts。"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from yomiage.batch.job_manager import BatchJobManager
from yomiage.ports.sources import URLPolicy
from yomiage.ports.tts import ProviderError, ProviderErrorCategory
from yomiage.server import ServerDependencies, create_app


class _Resolver:
    async def resolve(self, _hostname: str) -> tuple[str, ...]:
        return ("93.184.216.34",)


class _Supervisor:
    queue_depth = 0
    active_job_ids = frozenset()

    def __init__(self, *, submit_delay: float = 0.0) -> None:
        self.submit_delay = submit_delay
        self.tasks: set[asyncio.Task] = set()

    async def start(self) -> tuple:
        return ()

    async def submit(self, job, operation):
        if self.submit_delay:
            await asyncio.sleep(self.submit_delay)
        task = asyncio.create_task(operation(job))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def close(self) -> None:
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class _Engine:
    def __init__(self) -> None:
        self.is_running = False
        self.is_paused = False
        self.current_status = {}
        self.read_calls = 0
        self.tts = MagicMock()
        self.tts.synthesize_immediate = AsyncMock(
            return_value=SimpleNamespace(audio_data=b"RIFF", duration=0.01, format="wav")
        )

    async def read_url(self, _url: str) -> None:
        self.read_calls += 1
        self.is_running = True
        try:
            await asyncio.Event().wait()
        finally:
            self.is_running = False


class _LeaseEngine(_Engine):
    """Engine stateに頼らずread admission leaseだけを観測するfixture。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    async def read_url(self, _url: str) -> None:
        self.read_calls += 1
        self.started.set()
        await asyncio.to_thread(self.release.wait)


def _app(tmp_path, *, engine=None, supervisor=None):
    engine = engine or _Engine()
    supervisor = supervisor or _Supervisor()
    manager = BatchJobManager({})
    app = create_app(
        {
            "server": {"client_rate_limit": 1_000, "max_active_jobs": 20},
            "storage": {"sqlite_path": str(tmp_path / "jobs.sqlite3")},
        },
        ServerDependencies(
            engine=engine,
            job_manager=manager,
            supervisor=supervisor,
            url_policy=URLPolicy(resolver=_Resolver()),
        ),
    )
    return app, engine, manager


def test_f09_concurrent_read_admission_accepts_exactly_one(tmp_path) -> None:
    application, engine, _manager = _app(
        tmp_path, supervisor=_Supervisor(submit_delay=0.05)
    )
    with TestClient(application) as client:
        def submit() -> int:
            return client.post(
                "/api/yomiage/read", json={"url": "https://example.com/story"}
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(lambda _index: submit(), range(2)))
        deadline = time.monotonic() + 0.5
        while engine.read_calls < 1 and time.monotonic() < deadline:
            time.sleep(0.005)

    assert statuses == [200, 409]
    assert engine.read_calls == 1


def test_f09_read_admission_is_held_until_operation_finishes_then_released(tmp_path) -> None:
    engine = _LeaseEngine()
    supervisor = _Supervisor()
    application, _engine, _manager = _app(
        tmp_path,
        engine=engine,
        supervisor=supervisor,
    )
    with TestClient(application) as client:
        try:
            first = client.post(
                "/api/yomiage/read", json={"url": "https://example.com/story"}
            )
            assert first.status_code == 200
            assert engine.started.wait(timeout=0.5)

            while_running = client.post(
                "/api/yomiage/read", json={"url": "https://example.com/story"}
            )
            assert while_running.status_code == 409
            assert engine.read_calls == 1

            engine.release.set()
            deadline = time.monotonic() + 0.5
            while supervisor.tasks and time.monotonic() < deadline:
                time.sleep(0.005)

            after_finish = client.post(
                "/api/yomiage/read", json={"url": "https://example.com/story"}
            )
        finally:
            engine.release.set()

    assert after_finish.status_code == 200
    assert engine.read_calls == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "   "},
        {"text": "本文", "speed": -1},
        {"text": "本文", "speed": 6},
        {"text": "本文", "intonation": -0.1},
        {"text": "本文", "intonation": 2.1},
        {"text": "本文", "intensity": -0.1},
        {"text": "本文", "intensity": 2},
        {"text": "本文", "output_format": "unknown"},
    ],
)
def test_f13_invalid_immediate_request_is_4xx_before_tts(tmp_path, payload) -> None:
    application, engine, _manager = _app(tmp_path)
    with TestClient(application) as client:
        response = client.post("/api/yomiage/synthesize", json=payload)

    assert response.status_code == 422
    engine.tts.synthesize_immediate.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid",
    [
        {"mode": "unknown"},
        {"output_format": "unknown"},
    ],
)
def test_f13_unknown_batch_option_is_4xx_before_job_creation(
    tmp_path,
    invalid: dict[str, str],
) -> None:
    application, _engine, manager = _app(tmp_path)
    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/synthesize/batch",
            json={"url": "https://example.com/story", **invalid},
        )

    assert response.status_code == 422
    assert manager.list_jobs() == []


@pytest.mark.parametrize(
    ("category", "status"),
    [
        (ProviderErrorCategory.TIMEOUT, 504),
        (ProviderErrorCategory.UNAVAILABLE, 503),
        (ProviderErrorCategory.INVALID_RESPONSE, 502),
    ],
)
def test_f13_provider_errors_map_to_stable_http_status(
    tmp_path, category: ProviderErrorCategory, status: int
) -> None:
    engine = _Engine()
    engine.tts.synthesize_immediate = AsyncMock(
        side_effect=ProviderError("upstream", category=category, provider="fake")
    )
    application, _engine, _manager = _app(tmp_path, engine=engine)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/api/yomiage/synthesize", json={"text": "本文"})

    assert response.status_code == status
    assert response.json()["error"]["code"] == category.value
    assert "upstream" not in response.text


def test_f13_missing_sse_job_is_rejected_before_stream_start(tmp_path) -> None:
    application, _engine, _manager = _app(tmp_path)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/yomiage/synthesize/batch/missing/progress"
        )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
