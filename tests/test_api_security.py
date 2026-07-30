"""API-002 authentication, admission, and URL policy contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from yomiage.application.jobs import SupervisorClosedError, TaskQueueFullError
from yomiage.batch.job_manager import BatchJobManager
from yomiage.ports.sources import URLPolicy
from yomiage.server import ServerDependencies, create_app


class _Resolver:
    async def resolve(self, _hostname: str) -> tuple[str, ...]:
        return ("93.184.216.34",)


class _PrivateResolver:
    async def resolve(self, _hostname: str) -> tuple[str, ...]:
        return ("10.0.0.7",)


class _Supervisor:
    def __init__(
        self,
        *,
        queue_depth: int = 0,
        active: tuple[str, ...] = (),
        submit_error: Exception | None = None,
    ) -> None:
        self.queue_depth = queue_depth
        self.active_job_ids = frozenset(active)
        self.started = False
        self.submit_error = submit_error

    async def start(self) -> tuple[object, ...]:
        self.started = True
        return ()

    async def submit(self, job, _operation):
        if self.submit_error is not None:
            raise self.submit_error
        return job

    async def close(self) -> None:
        return None


def _app(tmp_path, **server):
    engine = MagicMock()
    engine.is_running = False
    engine.current_status = {"ready": True}
    engine.tts = MagicMock()
    engine.tts.synthesize_immediate = AsyncMock(
        return_value=SimpleNamespace(audio_data=b"RIFF", duration=0.01, format="wav")
    )
    manager = BatchJobManager({})
    supervisor = _Supervisor()
    config = {
        "server": {"api_token": "correct-token", "host": "0.0.0.0", **server},
        "storage": {"sqlite_path": str(tmp_path / "jobs.sqlite3")},
    }
    policy = URLPolicy(resolver=_Resolver())
    app = create_app(
        config,
        ServerDependencies(
            engine=engine,
            job_manager=manager,
            supervisor=supervisor,
            url_policy=policy,
        ),
    )
    return app, manager, supervisor


def test_bearer_auth_has_distinct_missing_and_invalid_semantics(tmp_path) -> None:
    app, _, _ = _app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

        missing = client.get("/api/yomiage/status")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert missing.json()["error"]["code"] == "authentication_required"

        malformed = client.get(
            "/api/yomiage/status", headers={"Authorization": "Basic correct-token"}
        )
        assert malformed.status_code == 401

        invalid = client.get(
            "/api/yomiage/status", headers={"Authorization": "Bearer wrong-token"}
        )
        assert invalid.status_code == 403
        assert "correct-token" not in invalid.text

        accepted = client.get(
            "/api/yomiage/status", headers={"Authorization": "Bearer correct-token"}
        )
        assert accepted.status_code == 200


def test_url_policy_is_propagated_to_source_consuming_dependencies(tmp_path) -> None:
    app, manager, _ = _app(tmp_path)
    with TestClient(app):
        assert app.state.engine.url_policy is app.state.url_policy
        assert manager.url_policy is app.state.url_policy


def test_body_and_text_limits_reject_before_job_creation(tmp_path) -> None:
    app, manager, _ = _app(
        tmp_path,
        max_body_bytes=128,
        max_text_chars=8,
        client_rate_limit=100,
    )
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(app) as client:
        body_too_large = client.post(
            "/api/yomiage/synthesize/batch",
            headers={**auth, "Content-Length": "9999"},
            content=b"{}",
        )
        assert body_too_large.status_code == 413
        assert manager.list_jobs() == []

        text_too_large = client.post(
            "/api/yomiage/synthesize",
            headers=auth,
            json={"text": "0123456789"},
        )
        assert text_too_large.status_code == 413
        assert manager.list_jobs() == []


def test_url_policy_rejects_before_batch_job_creation(tmp_path) -> None:
    app, manager, _ = _app(tmp_path, client_rate_limit=100)
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(app) as client:
        response = client.post(
            "/api/yomiage/synthesize/batch",
            headers=auth,
            json={"url": "http://127.0.0.1/private"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "url_not_allowed"
        assert manager.list_jobs() == []


def test_url_policy_rejects_private_dns_result_before_batch_job_creation(tmp_path) -> None:
    # Build the same application with a resolver that simulates DNS rebinding to RFC1918 space.
    engine = MagicMock()
    engine.is_running = False
    engine.current_status = {}
    engine.tts = MagicMock()
    manager = BatchJobManager({})
    application = create_app(
        {
            "server": {"api_token": "correct-token", "host": "0.0.0.0"},
            "storage": {"sqlite_path": str(tmp_path / "private.sqlite3")},
        },
        ServerDependencies(
            engine=engine,
            job_manager=manager,
            supervisor=_Supervisor(),
            url_policy=URLPolicy(resolver=_PrivateResolver()),
        ),
    )
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/synthesize/batch",
            headers=auth,
            json={"url": "https://rebind.example/story"},
        )
        assert response.status_code == 400
        assert manager.list_jobs() == []


def test_queue_limit_rejects_before_batch_job_creation(tmp_path) -> None:
    app, manager, supervisor = _app(
        tmp_path,
        job_queue_size=1,
        max_active_jobs=1,
        client_rate_limit=100,
    )
    supervisor.queue_depth = 1
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(app) as client:
        response = client.post(
            "/api/yomiage/synthesize/batch",
            headers=auth,
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "queue_full"
        assert manager.list_jobs() == []


def test_active_job_limit_rejects_before_batch_job_creation(tmp_path) -> None:
    app, manager, supervisor = _app(
        tmp_path,
        max_active_jobs=1,
        client_rate_limit=100,
    )
    supervisor.active_job_ids = frozenset({"already-running"})
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(app) as client:
        response = client.post(
            "/api/yomiage/synthesize/batch",
            headers=auth,
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "active_jobs_limit"
        assert manager.list_jobs() == []


def test_client_rate_limit_returns_429_without_creating_jobs(tmp_path) -> None:
    app, manager, _ = _app(tmp_path, client_rate_limit=1)
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(app) as client:
        assert client.get("/api/yomiage/status", headers=auth).status_code == 200
        response = client.post(
            "/api/yomiage/synthesize/batch",
            headers=auth,
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert manager.list_jobs() == []


def test_submit_race_maps_queue_failure_to_429_and_removes_job(tmp_path) -> None:
    engine = MagicMock()
    engine.is_running = False
    engine.current_status = {}
    engine.tts = MagicMock()
    manager = BatchJobManager({})
    application = create_app(
        {
            "server": {"api_token": "correct-token", "host": "0.0.0.0"},
            "storage": {"sqlite_path": str(tmp_path / "race.sqlite3")},
        },
        ServerDependencies(
            engine=engine,
            job_manager=manager,
            supervisor=_Supervisor(submit_error=TaskQueueFullError("raced")),
            url_policy=URLPolicy(resolver=_Resolver()),
        ),
    )
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/synthesize/batch",
            headers=auth,
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "queue_full"
        assert manager.list_jobs() == []


def test_submit_race_maps_closed_supervisor_to_429(tmp_path) -> None:
    engine = MagicMock()
    engine.is_running = False
    engine.current_status = {}
    engine.tts = MagicMock()
    manager = BatchJobManager({})
    application = create_app(
        {
            "server": {"api_token": "correct-token", "host": "0.0.0.0"},
            "storage": {"sqlite_path": str(tmp_path / "closed.sqlite3")},
        },
        ServerDependencies(
            engine=engine,
            job_manager=manager,
            supervisor=_Supervisor(submit_error=SupervisorClosedError("closed")),
            url_policy=URLPolicy(resolver=_Resolver()),
        ),
    )
    auth = {"Authorization": "Bearer correct-token"}
    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/synthesize/batch",
            headers=auth,
            json={"url": "https://example.com/story"},
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "admission_closed"
        assert manager.list_jobs() == []
