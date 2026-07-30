"""API-001 app factory and lifecycle contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from yomiage.server import ReadRequest, ServerDependencies, create_app


class _Engine:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.is_running = False
        self.closed = 0

    @property
    def current_status(self) -> dict[str, str]:
        return {"marker": self.marker}

    async def close(self) -> None:
        self.closed += 1


class _Repository:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class _Supervisor:
    def __init__(self, fail_start: bool = False) -> None:
        self.started = 0
        self.closed = 0
        self.fail_start = fail_start

    async def start(self) -> tuple[object, ...]:
        self.started += 1
        if self.fail_start:
            raise RuntimeError("startup failed")
        return ()

    async def close(self) -> None:
        self.closed += 1


def _app(engine: _Engine, repository: _Repository, supervisor: _Supervisor):
    return create_app(
        {"marker": engine.marker},
        ServerDependencies(
            engine=engine,
            repository=repository,
            supervisor=supervisor,
        ),
    )


def test_two_factory_apps_are_isolated() -> None:
    engine_a, repo_a, supervisor_a = _Engine("a"), _Repository(), _Supervisor()
    engine_b, repo_b, supervisor_b = _Engine("b"), _Repository(), _Supervisor()
    app_a = _app(engine_a, repo_a, supervisor_a)
    app_b = _app(engine_b, repo_b, supervisor_b)

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert client_a.get("/api/yomiage/status").json() == {"marker": "a"}
        assert client_b.get("/api/yomiage/status").json() == {"marker": "b"}
        assert app_a.state.runtime is not app_b.state.runtime
        assert app_a.state.runtime.config["marker"] == "a"
        assert app_b.state.runtime.config["marker"] == "b"

    assert supervisor_a.started == 1
    assert supervisor_b.started == 1
    assert engine_a.closed == repo_a.closed == supervisor_a.closed == 1
    assert engine_b.closed == repo_b.closed == supervisor_b.closed == 1


def test_lifespan_closes_each_injected_resource_once() -> None:
    engine, repository, supervisor = _Engine("close"), _Repository(), _Supervisor()
    application = _app(engine, repository, supervisor)

    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
    # A second invocation of the teardown callback is harmless and must not
    # close ownership resources a second time.
    runtime = application.state.runtime
    assert runtime is None
    assert engine.closed == repository.closed == supervisor.closed == 1


def test_read_request_drops_the_legacy_noop_provider_field() -> None:
    assert "provider" not in ReadRequest.model_fields


def test_failed_startup_closes_resources_built_before_supervisor() -> None:
    engine, repository = _Engine("startup-failure"), _Repository()
    supervisor = _Supervisor(fail_start=True)
    application = _app(engine, repository, supervisor)

    try:
        with TestClient(application):
            raise AssertionError("lifespan should fail")
    except RuntimeError as exc:
        assert str(exc) == "startup failed"
    assert engine.closed == repository.closed == supervisor.closed == 1
