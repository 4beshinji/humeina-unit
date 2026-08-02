"""FastAPI server and application factory.

The exported :data:`app` is kept as a legacy entry point, but the actual route
handlers only read the per-application runtime stored in ``app.state``.  New
callers should use :func:`create_app` and pass their settings/dependencies
explicitly; this makes two ASGI applications safe to host in one process.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import io
import ipaddress
import json
import secrets
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel

from .adapters.persistence.sqlite import SQLiteJobRepository
from .api.hooks import EventHooks
from .api.metrics import MetricsCollector
from .application.health import CHECK_NAMES, HealthCheck, HealthChecker, HealthReport
from .application.jobs import SupervisorClosedError, TaskQueueFullError, TaskSupervisor
from .batch.job_manager import BatchJobManager
from .bootstrap import create_reading_engine
from .config import load_config
from .core.identity import new_job_id
from .core.jobs import Job
from .nlp.ollama_client import OllamaClient
from .ports.repositories import JobRepository
from .ports.sources import DNSResolutionError, URLPolicy, URLPolicyError, URLTarget


class _AdmissionRejected(Exception):
    """Internal signal carrying a quota rejection code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _SecurityConfig:
    """Resolved API security and admission limits for one application."""

    host: str = "127.0.0.1"
    api_token: str | None = None
    max_body_bytes: int = 1_048_576
    max_text_chars: int = 100_000
    max_url_response_bytes: int = 10 * 1024 * 1024
    max_queue_size: int = 64
    max_active_jobs: int = 65
    client_rate_limit: int = 60
    client_rate_window_seconds: float = 60.0


@dataclass(slots=True)
class _AdmissionController:
    """Single-loop admission gate for jobs and per-client request rates."""

    max_active_jobs: int
    max_queue_size: int
    rate_limit: int
    rate_window_seconds: float
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _leases: dict[str, str] = field(default_factory=dict)
    _requests: dict[str, list[float]] = field(default_factory=dict)

    async def check_rate(self, client_id: str) -> None:
        """Record one request and reject only when its client rate is exhausted."""

        now = time.monotonic()
        async with self._lock:
            requests = self._requests.setdefault(client_id, [])
            cutoff = now - self.rate_window_seconds
            while requests and requests[0] <= cutoff:
                requests.pop(0)
            if len(requests) >= self.rate_limit:
                raise _AdmissionRejected(
                    "rate_limited", "client request rate limit exceeded"
                )
            requests.append(now)

    async def reserve(
        self,
        client_id: str,
        *,
        active_jobs: int = 0,
        queue_depth: int = 0,
    ) -> str:
        """Reserve one admission slot before a Job or temporary artifact exists."""

        async with self._lock:
            reserved = len(self._leases)
            if queue_depth + reserved >= self.max_queue_size:
                raise _AdmissionRejected("queue_full", "job queue limit exceeded")
            if active_jobs + queue_depth + reserved >= self.max_active_jobs:
                raise _AdmissionRejected("active_jobs_limit", "active job limit exceeded")
            lease = secrets.token_urlsafe(16)
            self._leases[lease] = client_id
            return lease

    async def release(self, lease: str | None) -> None:
        if lease is None:
            return
        async with self._lock:
            self._leases.pop(lease, None)

    @property
    def active_leases(self) -> int:
        return len(self._leases)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Return the API's stable, secret-free error DTO."""

    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=dict(headers or {}),
    )


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _server_security(config: Mapping[str, Any]) -> _SecurityConfig:
    raw = config.get("server", {})
    if not isinstance(raw, Mapping):
        raw = {}
    token = raw.get("api_token")
    if hasattr(token, "get_secret_value"):
        token = token.get_secret_value()
    if token is not None:
        token = str(token).strip() or None
    max_text = int(
        raw.get(
            "max_text_chars",
            raw.get("text_max_chars", raw.get("max_request_chars", 100_000)),
        )
    )
    max_queue = int(
        raw.get("job_queue_size", raw.get("max_queue_length", raw.get("queue_size", 64)))
    )
    max_concurrent = int(raw.get("max_concurrent_jobs", 1))
    max_active_raw = raw.get("max_active_jobs", raw.get("active_job_limit"))
    max_active = int(max_active_raw) if max_active_raw is not None else max_queue + max_concurrent
    max_body = int(raw.get("max_body_bytes", raw.get("max_request_body_bytes", 1_048_576)))
    max_url_bytes = int(
        raw.get("max_url_response_bytes", raw.get("max_fetch_bytes", 10 * 1024 * 1024))
    )
    rate_limit = int(raw.get("client_rate_limit", raw.get("rate_limit_per_minute", 60)))
    rate_window = float(
        raw.get("client_rate_window_seconds", raw.get("rate_limit_window_seconds", 60.0))
    )
    if min(max_text, max_queue, max_active, max_body, max_url_bytes, rate_limit) <= 0:
        raise ValueError("API limits must be positive")
    if rate_window <= 0:
        raise ValueError("client rate window must be positive")
    return _SecurityConfig(
        host=str(raw.get("host", "127.0.0.1")),
        api_token=token,
        max_body_bytes=max_body,
        max_text_chars=max_text,
        max_url_response_bytes=max_url_bytes,
        max_queue_size=max_queue,
        max_active_jobs=max_active,
        client_rate_limit=rate_limit,
        client_rate_window_seconds=rate_window,
    )


def _client_id(scope: Mapping[str, Any]) -> str:
    """Use the direct ASGI peer only; X-Forwarded-For is deliberately ignored."""

    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return "unknown"


class _APISecurityMiddleware:
    """ASGI auth/body/rate gate that runs before FastAPI parses request bodies."""

    _PUBLIC_PATHS = frozenset({"/health", "/health/live"})

    def __init__(self, app: Any, *, application: FastAPI) -> None:
        self.app = app
        self.application = application

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        runtime = getattr(self.application.state, "runtime", None)
        security = getattr(runtime, "security", None)
        if security is None:
            # The compatibility app can receive requests without lifespan. Build the same
            # conservative loopback/no-token defaults instead of consulting module globals.
            security = _server_security(getattr(runtime, "config", {}) if runtime else {})

        path = str(scope.get("path", ""))
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }
        protected = path not in self._PUBLIC_PATHS
        if protected and security.api_token:
            authorization = headers.get("authorization", "")
            if not authorization.startswith("Bearer "):
                await self._send_error(
                    scope,
                    send,
                    _error_response(
                        401,
                        "authentication_required",
                        "Bearer authentication is required",
                        headers={"WWW-Authenticate": "Bearer"},
                    ),
                )
                return
            supplied = authorization[7:]
            if not supplied or not hmac.compare_digest(supplied, security.api_token):
                await self._send_error(
                    scope,
                    send,
                    _error_response(403, "authentication_forbidden", "Bearer token is invalid"),
                )
                return

        content_length = headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                declared = security.max_body_bytes + 1
            if declared > security.max_body_bytes:
                await self._send_error(
                    scope,
                    send,
                    _error_response(413, "request_body_too_large", "request body is too large"),
                )
                return

        # Rate limiting is intentionally keyed by the direct peer address. It does not trust
        # proxy headers, and a failed admission never creates a Job or artifact.
        admission = getattr(runtime, "admission", None)
        if protected and admission is not None:
            try:
                await admission.check_rate(_client_id(scope))
            except _AdmissionRejected as exc:
                status = 429
                await self._send_error(scope, send, _error_response(status, exc.code, exc.message))
                return

        # Buffer only up to the configured cap before FastAPI/Pydantic sees the request. This
        # avoids turning an oversized chunked body into Starlette's generic 400 parser error and
        # guarantees a stable 413 response before any route-side object is constructed.
        buffered: deque[dict[str, Any]] = deque()
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > security.max_body_bytes:
                    await self._send_error(
                        scope,
                        send,
                        _error_response(413, "request_body_too_large", "request body is too large"),
                    )
                    return
            if not message.get("more_body", False):
                break

        async def limited_receive() -> dict[str, Any]:
            if buffered:
                return buffered.popleft()
            # Streaming responses keep listening for client disconnects while the response
            # body is sent. Returning a synthetic disconnect here cancels those responses
            # immediately after the request body is consumed.
            return await receive()

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _send_error(scope: Mapping[str, Any], send: Any, response: JSONResponse) -> None:
        await response(scope=dict(scope), receive=None, send=send)

# These names are intentionally retained for old callers/tests that imported
# them.  Factory-created applications never read them.  The compatibility app
# below uses them only while constructing its legacy composition root.
_engine: Any | None = None
_job_manager: BatchJobManager | None = None
_hooks: EventHooks | None = None
_metrics: MetricsCollector | None = None
_config: dict[str, Any] = {}
_job_repository: SQLiteJobRepository | None = None
_supervisor: TaskSupervisor | None = None
_compat_tasks: set[asyncio.Task[Any]] = set()


def _create_engine(
    config: Mapping[str, Any],
    *,
    hooks: EventHooks | None = None,
    metrics: MetricsCollector | None = None,
) -> Any:
    """Compatibility hook delegating composition to :mod:`yomiage.bootstrap`."""

    return create_reading_engine(config, hooks=hooks, metrics=metrics)


def _job_database_path(config: Mapping[str, Any]) -> Path:
    """Resolve a single-host Job DB path from a raw configuration mapping."""

    storage = config.get("storage", {})
    if not isinstance(storage, Mapping):
        storage = {}
    configured = storage.get("sqlite_path") or storage.get("job_db_path")
    if configured:
        return Path(str(configured)).expanduser().resolve()
    data_dir = storage.get("data_dir") or config.get("data_dir") or (Path.cwd() / "data")
    return (Path(str(data_dir)).expanduser() / "jobs.sqlite3").resolve()


def _as_config(settings: Any) -> dict[str, Any]:
    """Convert Settings/pydantic/raw mappings to the legacy provider mapping."""

    if isinstance(settings, Mapping):
        return dict(settings)
    to_legacy = getattr(settings, "to_legacy_dict", None)
    if callable(to_legacy):
        value = to_legacy()
        if isinstance(value, Mapping):
            return dict(value)
    model_dump = getattr(settings, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="python")
        if isinstance(value, Mapping):
            return dict(value)
    raise TypeError("settings must be a mapping or a Settings-like object")


@dataclass(slots=True)
class ServerDependencies:
    """Composition-root overrides for :func:`create_app`.

    Values may be supplied directly, or through ``*_factory`` callables.  A
    supplied resource is owned by the application by default; set the matching
    ``close_*`` flag to ``False`` for a process-level singleton managed by the
    caller.  Factories receive only values they declare (``config``, ``hooks``,
    ``metrics``, ``repository``, ``supervisor`` and ``job_manager``).
    """

    config: Mapping[str, Any] | None = None
    config_factory: Callable[..., Any] | None = None
    engine: Any | None = None
    engine_factory: Callable[..., Any] | None = None
    job_manager: Any | None = None
    job_manager_factory: Callable[..., Any] | None = None
    hooks: EventHooks | None = None
    hooks_factory: Callable[..., Any] | None = None
    metrics: MetricsCollector | None = None
    metrics_factory: Callable[..., Any] | None = None
    health: Any | None = None
    health_factory: Callable[..., Any] | None = None
    health_checker: Any | None = None
    health_checker_factory: Callable[..., Any] | None = None
    health_dependencies: Any | None = None
    repository: JobRepository | None = None
    repository_factory: Callable[..., Any] | None = None
    supervisor: TaskSupervisor | None = None
    supervisor_factory: Callable[..., Any] | None = None
    url_policy: URLPolicy | None = None
    url_policy_factory: Callable[..., Any] | None = None
    close_engine: bool = True
    close_job_manager: bool = True
    close_repository: bool = True
    close_supervisor: bool = True
    legacy_global_mirror: bool = False


@dataclass(slots=True)
class _Runtime:
    config: dict[str, Any]
    engine: Any | None
    job_manager: Any | None
    hooks: EventHooks | None
    metrics: MetricsCollector | None
    repository: JobRepository | None
    supervisor: TaskSupervisor | None
    health: Any | None = None
    security: _SecurityConfig | None = None
    admission: _AdmissionController | None = None
    url_policy: URLPolicy | None = None
    url_policy_explicit: bool = False
    compat_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    close_engine: bool = True
    close_job_manager: bool = True
    close_repository: bool = True
    close_supervisor: bool = True
    closed: bool = False


def _dependency(dependencies: ServerDependencies, name: str) -> Any:
    return getattr(dependencies, name)


def _call_factory(factory: Callable[..., Any], **values: Any) -> Any:
    """Call a dependency factory while accepting simple one-argument lambdas."""

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):  # pragma: no cover - exotic callable
        return factory(values.get("config"))
    parameters = signature.parameters
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return factory(**values)
    kwargs = {name: value for name, value in values.items() if name in parameters}
    required_positional = [
        param
        for param in parameters.values()
        if param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and param.default is inspect.Parameter.empty
    ]
    if required_positional and not kwargs:
        if len(required_positional) == 1:
            return factory(values.get(required_positional[0].name, values.get("config")))
        return factory(*[values.get(param.name) for param in required_positional])
    return factory(**kwargs)


async def _resolve_factory(factory: Callable[..., Any], **values: Any) -> Any:
    result = _call_factory(factory, **values)
    if inspect.isawaitable(result):
        return await result
    return result


async def _resolve_settings(settings: Any, dependencies: ServerDependencies) -> dict[str, Any]:
    if settings is not None:
        return _as_config(settings)
    if dependencies.config is not None:
        return _as_config(dependencies.config)
    if dependencies.config_factory is not None:
        return _as_config(await _resolve_factory(dependencies.config_factory))
    return load_config()


async def _await_close(value: Any, label: str) -> None:
    close = None
    for method_name in ("close", "shutdown", "stop"):
        candidate = getattr(value, method_name, None)
        if callable(candidate):
            # TTSManager/TaskSupervisor expose ``stop``/``shutdown`` as their
            # historical lifecycle methods. Prefer close() when available so
            # a resource is invoked exactly once.
            close = candidate
            break
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # pragma: no cover - provider-specific cleanup
        logger.warning("{} cleanup failed: {}", label, exc)


async def _close_engine_resources(engine: Any) -> None:
    """Stop an engine and close each provider/session at most once."""

    if engine is None:
        return
    # A custom engine may own all of its resources behind close().
    engine_close = getattr(engine, "close", None)
    if callable(engine_close):
        try:
            result = engine_close()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # pragma: no cover - provider-specific cleanup
            logger.warning("Engine cleanup failed: {}", exc)
        return
    if getattr(engine, "is_running", False):
        stop = getattr(engine, "stop", None)
        if callable(stop):
            try:
                result = stop()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # pragma: no cover - provider-specific cleanup
                logger.warning("Engine stop failed: {}", exc)
    tts = getattr(engine, "tts", None)
    if tts is None:
        return
    owns_service = bool(getattr(tts, "owns_service", False))
    await _await_close(tts, "TTS worker")
    if owns_service or bool(getattr(tts, "providers_closed", False)):
        # The canonical service closes its adapted providers.  Calling the legacy
        # providers again would violate the exactly-once lifecycle contract.
        return
    seen: set[int] = set()
    for provider in (getattr(tts, "primary", None), getattr(tts, "fallback", None)):
        if provider is None or id(provider) in seen:
            continue
        seen.add(id(provider))
        await _await_close(provider, "TTS provider")


async def _build_runtime(settings: Any, dependencies: ServerDependencies) -> _Runtime:
    config = await _resolve_settings(settings, dependencies)
    security = _server_security(config)
    if not _is_loopback_host(security.host) and not security.api_token:
        raise ValueError("non-loopback API bind requires an API token")
    hooks = dependencies.hooks
    if hooks is None and dependencies.hooks_factory is not None:
        hooks = await _resolve_factory(dependencies.hooks_factory, config=config)
    hooks = hooks or EventHooks()
    metrics = dependencies.metrics
    if metrics is None and dependencies.metrics_factory is not None:
        metrics = await _resolve_factory(dependencies.metrics_factory, config=config)
    metrics = metrics or MetricsCollector()
    runtime = _Runtime(
        config=config,
        engine=None,
        job_manager=None,
        hooks=hooks,
        metrics=metrics,
        repository=None,
        supervisor=None,
        close_engine=dependencies.close_engine,
        close_job_manager=dependencies.close_job_manager,
        close_repository=dependencies.close_repository,
        close_supervisor=dependencies.close_supervisor,
        security=security,
        admission=_AdmissionController(
            max_active_jobs=security.max_active_jobs,
            max_queue_size=security.max_queue_size,
            rate_limit=security.client_rate_limit,
            rate_window_seconds=security.client_rate_window_seconds,
        ),
    )
    try:
        values = {"config": config, "settings": config, "hooks": hooks, "metrics": metrics}
        runtime.engine = dependencies.engine
        if runtime.engine is None and dependencies.engine_factory is not None:
            runtime.engine = await _resolve_factory(dependencies.engine_factory, **values)
        if runtime.engine is None:
            runtime.engine = _create_engine(config, hooks=hooks, metrics=metrics)

        runtime.job_manager = dependencies.job_manager
        if runtime.job_manager is None and dependencies.job_manager_factory is not None:
            runtime.job_manager = await _resolve_factory(
                dependencies.job_manager_factory,
                config=config,
                settings=config,
                hooks=hooks,
                metrics=metrics,
            )
        if runtime.job_manager is None:
            batch_cfg = config.get("batch", {})
            if not isinstance(batch_cfg, Mapping):
                batch_cfg = {}
            runtime.job_manager = BatchJobManager(
                config,
                output_dir=str(batch_cfg.get("output_dir", "output")),
                hooks=hooks,
                metrics=metrics,
            )

        runtime.repository = dependencies.repository
        if runtime.repository is None and dependencies.repository_factory is not None:
            runtime.repository = await _resolve_factory(
                dependencies.repository_factory, **values
            )
        if runtime.repository is None:
            runtime.repository = SQLiteJobRepository(_job_database_path(config))

        # Health is an application query, not a global singleton.  Tests and
        # embedders may inject a complete checker or small fake; the default
        # checker derives provider/LLM state from the composed engine and checks
        # local storage/SQLite/ffmpeg without exposing raw exception details.
        runtime.health = dependencies.health
        if runtime.health is None:
            runtime.health = dependencies.health_checker
        health_factory = dependencies.health_factory or dependencies.health_checker_factory
        if runtime.health is None and health_factory is not None:
            runtime.health = await _resolve_factory(
                health_factory,
                config=config,
                settings=config,
                engine=runtime.engine,
                repository=runtime.repository,
                metrics=metrics,
                dependencies=dependencies.health_dependencies,
            )
        if runtime.health is None:
            runtime.health = HealthChecker(
                engine=runtime.engine,
                repository=runtime.repository,
                config=config,
                dependencies=dependencies.health_dependencies,
            )

        if dependencies.url_policy is not None:
            runtime.url_policy = dependencies.url_policy
            runtime.url_policy_explicit = True
        elif dependencies.url_policy_factory is not None:
            runtime.url_policy = await _resolve_factory(
                dependencies.url_policy_factory, config=config
            )
            runtime.url_policy_explicit = True
        else:
            policy_config = config.get("url_policy", {})
            if not isinstance(policy_config, Mapping):
                policy_config = {}
            server_policy = config.get("server", {})
            if isinstance(server_policy, Mapping) and isinstance(
                server_policy.get("url_policy"), Mapping
            ):
                policy_config = {**policy_config, **server_policy["url_policy"]}
            allowlist = policy_config.get("allowlist", ())
            if isinstance(allowlist, str):
                allowlist = (allowlist,)
            runtime.url_policy = URLPolicy(
                max_response_bytes=int(
                    policy_config.get("max_response_bytes", security.max_url_response_bytes)
                ),
                max_redirects=int(policy_config.get("max_redirects", 5)),
                timeout_seconds=float(policy_config.get("timeout_seconds", 30.0)),
                allowlist=frozenset(allowlist),
                allow_private_network=bool(policy_config.get("allow_private_network", False)),
            )
        # Propagate the validated policy into concrete source-consuming engines.  This keeps the
        # response-size/redirect limits identical between API admission and the eventual fetch.
        for owner in (runtime.engine, runtime.job_manager):
            if owner is None:
                continue
            try:
                setattr(owner, "url_policy", runtime.url_policy)
            except AttributeError:  # pragma: no cover - slotted third-party dependency
                pass

        runtime.supervisor = dependencies.supervisor
        if runtime.supervisor is None and dependencies.supervisor_factory is not None:
            runtime.supervisor = await _resolve_factory(
                dependencies.supervisor_factory,
                config=config,
                settings=config,
                repository=runtime.repository,
                job_manager=runtime.job_manager,
            )
        if runtime.supervisor is None:
            server_cfg = config.get("server", {})
            if not isinstance(server_cfg, Mapping):
                server_cfg = {}
            runtime.supervisor = TaskSupervisor(
                runtime.repository,
                max_concurrency=int(server_cfg.get("max_concurrent_jobs", 1)),
                queue_size=int(server_cfg.get("job_queue_size", 64)),
                shutdown_grace_period=float(server_cfg.get("shutdown_grace_period", 5.0)),
            )

        start = getattr(runtime.supervisor, "start", None)
        if callable(start):
            recovered = await start()
            if recovered:
                logger.warning(
                    "Recovered {} jobs after restart: {}",
                    len(recovered),
                    ", ".join(job.id for job in recovered),
                )
    except BaseException:
        await _teardown_runtime(runtime)
        raise
    return runtime


async def _teardown_runtime(runtime: _Runtime) -> None:
    if runtime.closed:
        return
    runtime.closed = True
    if runtime.close_supervisor:
        await _await_close(runtime.supervisor, "TaskSupervisor")
    if runtime.close_engine:
        await _close_engine_resources(runtime.engine)
    if runtime.close_job_manager:
        await _await_close(runtime.job_manager, "BatchJobManager")
    if runtime.close_repository:
        await _await_close(runtime.repository, "JobRepository")
    for task in tuple(runtime.compat_tasks):
        if not task.done():
            task.cancel()
    if runtime.compat_tasks:
        await asyncio.gather(*runtime.compat_tasks, return_exceptions=True)
    runtime.compat_tasks.clear()


def _legacy_engine_factory(config: Mapping[str, Any], **values: Any) -> Any:
    if _engine is not None:
        return _engine
    return _call_factory(
        _create_engine,
        config=config,
        hooks=values.get("hooks"),
        metrics=values.get("metrics"),
    )


def _legacy_manager_factory(config: Mapping[str, Any], **values: Any) -> Any:
    if _job_manager is not None:
        return _job_manager
    batch_cfg = config.get("batch", {})
    if not isinstance(batch_cfg, Mapping):
        batch_cfg = {}
    return BatchJobManager(
        config,
        output_dir=str(batch_cfg.get("output_dir", "output")),
        hooks=values.get("hooks"),
        metrics=values.get("metrics"),
    )


def _legacy_hooks_factory(**_values: Any) -> EventHooks:
    return _hooks or EventHooks()


def _legacy_metrics_factory(**_values: Any) -> MetricsCollector:
    return _metrics or MetricsCollector()


def _legacy_repository_factory(config: Mapping[str, Any], **_values: Any) -> JobRepository:
    return _job_repository or SQLiteJobRepository(_job_database_path(config))


def _legacy_supervisor_factory(
    repository: JobRepository, config: Mapping[str, Any], **_values: Any
) -> Any:
    if _supervisor is not None:
        return _supervisor
    server_cfg = config.get("server", {})
    if not isinstance(server_cfg, Mapping):
        server_cfg = {}
    return TaskSupervisor(
        repository,
        max_concurrency=int(server_cfg.get("max_concurrent_jobs", 1)),
        queue_size=int(server_cfg.get("job_queue_size", 64)),
        shutdown_grace_period=float(server_cfg.get("shutdown_grace_period", 5.0)),
    )


def _legacy_dependencies() -> ServerDependencies:
    return ServerDependencies(
        config_factory=lambda: load_config(),
        engine_factory=_legacy_engine_factory,
        job_manager_factory=_legacy_manager_factory,
        hooks_factory=_legacy_hooks_factory,
        metrics_factory=_legacy_metrics_factory,
        repository_factory=_legacy_repository_factory,
        supervisor_factory=_legacy_supervisor_factory,
        legacy_global_mirror=True,
    )


def _legacy_ephemeral_runtime() -> _Runtime:
    """Support old ``TestClient(app)`` calls that do not enter lifespan."""

    config = _config or {}
    return _Runtime(
        config=config,
        engine=_engine,
        job_manager=_job_manager,
        hooks=_hooks,
        metrics=_metrics,
        repository=_job_repository,
        supervisor=_supervisor,
        health=HealthChecker(
            engine=_engine,
            repository=_job_repository,
            config=config,
        ),
        security=_server_security(_config or {}),
        admission=_AdmissionController(
            max_active_jobs=_server_security(_config or {}).max_active_jobs,
            max_queue_size=_server_security(_config or {}).max_queue_size,
            rate_limit=_server_security(_config or {}).client_rate_limit,
            rate_window_seconds=_server_security(_config or {}).client_rate_window_seconds,
        ),
        url_policy=URLPolicy(),
        compat_tasks=_compat_tasks,
        close_engine=False,
        close_job_manager=False,
        close_repository=False,
        close_supervisor=False,
    )


def _runtime(request: Request) -> _Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        return runtime
    # Only the exported compatibility app may use the old module-level bridge.
    if getattr(request.app.state, "legacy_compat", False):
        return _legacy_ephemeral_runtime()
    raise HTTPException(503, "Application runtime not initialized")


async def _health_method(runtime: _Runtime, names: tuple[str, ...]) -> Any:
    health = runtime.health
    if health is None:
        health = HealthChecker(
            engine=runtime.engine,
            repository=runtime.repository,
            config=runtime.config,
        )
    for name in names:
        method = getattr(health, name, None)
        if callable(method):
            value = method()
            return await value if inspect.isawaitable(value) else value
    return None


def _ready_report(value: Any) -> HealthReport:
    if isinstance(value, HealthReport):
        return value
    if isinstance(value, Mapping):
        raw_checks = value.get("checks", {})
        checks = raw_checks if isinstance(raw_checks, Mapping) else {}
        ready = value.get("ready")
        if ready is None:
            ready = str(value.get("status", "")).lower() in {"ok", "ready", "healthy"}
        return HealthReport(bool(ready), {str(name): item for name, item in checks.items()})
    if isinstance(value, bool):
        return HealthReport(value, {})
    return HealthReport(False, {})


def _failed_health_report() -> HealthReport:
    return HealthReport(
        False,
        {name: HealthCheck(False, "check_failed") for name in CHECK_NAMES},
    )


async def _submit_background_job(
    runtime: _Runtime,
    job: Job,
    operation: Any,
    *,
    lease: str | None = None,
) -> Job:
    async def guarded_operation(item: Job) -> Any:
        try:
            return await operation(item)
        finally:
            if runtime.admission is not None:
                await runtime.admission.release(lease)

    if runtime.supervisor is not None:
        try:
            result = await runtime.supervisor.submit(job, guarded_operation)
            # The supervisor's queue/active registry owns capacity after submit.  Release the
            # pre-submit lease immediately so a queued cancellation cannot strand an admission
            # slot (the guarded callback also releases idempotently when it runs).
            if runtime.admission is not None:
                await runtime.admission.release(lease)
            return result
        except (TaskQueueFullError, SupervisorClosedError) as exc:
            if runtime.admission is not None:
                await runtime.admission.release(lease)
            code = "queue_full" if isinstance(exc, TaskQueueFullError) else "admission_closed"
            raise HTTPException(
                429,
                detail={"code": code, "message": "job admission limit exceeded"},
            ) from exc
        except BaseException:
            if runtime.admission is not None:
                await runtime.admission.release(lease)
            raise
    task = asyncio.create_task(guarded_operation(job), name=f"yomiage-compat-job-{job.id}")
    runtime.compat_tasks.add(task)
    task.add_done_callback(runtime.compat_tasks.discard)
    return job


def _supervisor_counts(runtime: _Runtime) -> tuple[int, int]:
    supervisor = runtime.supervisor
    if supervisor is None:
        return 0, 0
    active = len(getattr(supervisor, "active_job_ids", ()))
    queue = int(getattr(supervisor, "queue_depth", 0))
    return active, queue


async def _reserve_job(runtime: _Runtime, request: Request) -> str | None:
    admission = runtime.admission
    if admission is None:
        return None
    active, queue = _supervisor_counts(runtime)
    try:
        return await admission.reserve(
            _client_id(request.scope), active_jobs=active, queue_depth=queue
        )
    except _AdmissionRejected as exc:
        raise HTTPException(429, detail={"code": exc.code, "message": exc.message}) from exc


async def _validate_url(runtime: _Runtime, url: str) -> URLTarget:
    policy = runtime.url_policy or URLPolicy()
    try:
        # Resolve and validate every hostname before admission.  This closes the DNS-rebinding
        # and private-network path before a Job or temporary artifact exists.  Test callers can
        # inject a deterministic resolver through ``ServerDependencies.url_policy``.
        return await policy.validate(url)
    except (URLPolicyError, DNSResolutionError) as exc:
        raise HTTPException(
            400,
            detail={"code": "url_not_allowed", "message": "URL is not allowed"},
        ) from exc


async def _reserve_text_job(runtime: _Runtime, request: Request, text: str) -> str | None:
    if len(text) > (runtime.security.max_text_chars if runtime.security else 100_000):
        raise HTTPException(
            413,
            detail={"code": "text_too_large", "message": "text exceeds the configured limit"},
        )
    return await _reserve_job(runtime, request)


class ReadRequest(BaseModel):
    url: str
    # ``provider`` used to be accepted but was never applied.  It is omitted
    # intentionally; Pydantic's default ``extra='ignore'`` keeps old payloads
    # wire-compatible while preventing a misleading no-op option.


class SynthesizeRequest(BaseModel):
    text: str
    voice_id: str | None = None
    speed: float = 1.0
    pitch: float = 0.0
    volume: float = 0.0
    intonation: float = 1.0
    preset: str | None = None
    emotion: str = "neutral"
    intensity: float = 0.5
    output_format: str = "wav"


class BatchSynthesizeRequest(BaseModel):
    url: str
    mode: str = "voicevox"
    output_format: str = "wav"
    video: bool = False
    style: str | None = None
    allow_partial: bool = False


def create_app(
    settings: Any = None,
    dependencies: ServerDependencies | Mapping[str, Any] | None = None,
) -> FastAPI:
    """Create an isolated FastAPI application.

    ``settings`` accepts a raw config mapping or a typed Settings object.  All
    runtime objects are constructed by the app lifespan and stored under
    ``app.state.runtime``; no request handler consults module globals.
    """

    if dependencies is None:
        deps = ServerDependencies()
    elif isinstance(dependencies, ServerDependencies):
        deps = dependencies
    elif isinstance(dependencies, Mapping):
        allowed = {
            key: value
            for key, value in dependencies.items()
            if hasattr(ServerDependencies, key)
        }
        deps = ServerDependencies(**allowed)
    else:
        values = {
            field_name: getattr(dependencies, field_name)
            for field_name in ServerDependencies.__dataclass_fields__
            if hasattr(dependencies, field_name)
        }
        deps = ServerDependencies(**values)

    @asynccontextmanager
    async def app_lifespan(application: FastAPI):
        nonlocal deps
        runtime = await _build_runtime(settings, deps)
        application.state.runtime = runtime
        application.state.engine = runtime.engine
        application.state.job_manager = runtime.job_manager
        application.state.hooks = runtime.hooks
        application.state.metrics = runtime.metrics
        application.state.health = runtime.health
        application.state.job_repository = runtime.repository
        application.state.task_supervisor = runtime.supervisor
        application.state.security = runtime.security
        application.state.admission = runtime.admission
        application.state.url_policy = runtime.url_policy
        if deps.legacy_global_mirror:
            global _engine, _job_manager, _hooks, _metrics, _config, _job_repository, _supervisor
            _engine = runtime.engine
            _job_manager = runtime.job_manager
            _hooks = runtime.hooks
            _metrics = runtime.metrics
            _config = runtime.config
            _job_repository = (
                runtime.repository
                if isinstance(runtime.repository, SQLiteJobRepository)
                else None
            )
            _supervisor = runtime.supervisor
        try:
            yield
        finally:
            await _teardown_runtime(runtime)
            for name in (
                "runtime",
                "engine",
                "job_manager",
                "hooks",
                "metrics",
                "health",
                "job_repository",
                "task_supervisor",
                "security",
                "admission",
                "url_policy",
            ):
                setattr(application.state, name, None)
            if deps.legacy_global_mirror:
                _engine = None
                _job_manager = None
                _hooks = None
                _metrics = None
                _config = {}
                _job_repository = None
                _supervisor = None

    application = FastAPI(title="Yomiage Server", version="0.1.0", lifespan=app_lifespan)
    application.state.legacy_compat = deps.legacy_global_mirror
    application.add_middleware(_APISecurityMiddleware, application=application)

    @application.exception_handler(HTTPException)
    async def _http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        status = int(exc.status_code)
        detail = exc.detail
        if isinstance(detail, Mapping):
            code = str(detail.get("code", f"http_{status}"))
            message = str(detail.get("message", "request failed"))
        else:
            code = {
                401: "authentication_required",
                403: "authentication_forbidden",
                413: "request_body_too_large",
                429: "rate_limited",
            }.get(status, f"http_{status}")
            message = str(detail or "request failed")
        headers = dict(exc.headers or {})
        if status == 401:
            headers.setdefault("WWW-Authenticate", "Bearer")
        return _error_response(status, code, message, headers=headers)

    @application.get("/health")
    async def health(request: Request):
        runtime = _runtime(request)
        engine = runtime.engine
        return {"status": "ok", "reading": bool(engine and engine.is_running)}

    @application.get("/health/live")
    async def health_live(request: Request):
        runtime = _runtime(request)
        try:
            value = await _health_method(runtime, ("liveness", "live"))
            if isinstance(value, Mapping):
                payload = dict(value)
                payload.setdefault("status", "ok")
                payload.setdefault("live", True)
                return payload
        except Exception:
            # Liveness intentionally remains successful when a dependency probe
            # is broken; it only answers whether this process can serve HTTP.
            pass
        return {"status": "ok", "live": True}

    @application.get("/health/ready")
    async def health_ready(request: Request):
        runtime = _runtime(request)
        try:
            report = _ready_report(await _health_method(runtime, ("readiness", "ready")))
        except Exception:
            report = _failed_health_report()
        payload = report.to_dict()
        return JSONResponse(status_code=200 if report.ready else 503, content=payload)

    @application.get("/api/yomiage/status")
    async def status(request: Request):
        engine = _runtime(request).engine
        if engine is None:
            raise HTTPException(503, "Engine not initialized")
        return engine.current_status

    @application.post("/api/yomiage/read")
    async def read(req: ReadRequest, request: Request):
        runtime = _runtime(request)
        engine = runtime.engine
        if engine is None:
            raise HTTPException(503, "Engine not initialized")
        if engine.is_running:
            raise HTTPException(409, "Already reading")
        target = await _validate_url(runtime, req.url)
        lease = await _reserve_job(runtime, request)
        job = Job(id=new_job_id(), options={"kind": "read"})

        async def operation(_job: Job) -> None:
            await engine.read_url(target.url)

        try:
            await _submit_background_job(runtime, job, operation, lease=lease)
        except BaseException:
            if runtime.admission is not None:
                await runtime.admission.release(lease)
            raise
        return {"status": "started", "url": target.url, "job_id": job.id}

    @application.post("/api/yomiage/pause")
    async def pause(request: Request):
        engine = _runtime(request).engine
        if engine and engine.is_running:
            engine.pause()
            return {"status": "paused"}
        raise HTTPException(409, "Not reading")

    @application.post("/api/yomiage/resume")
    async def resume(request: Request):
        engine = _runtime(request).engine
        if engine and engine.is_paused:
            engine.resume()
            return {"status": "resumed"}
        raise HTTPException(409, "Not paused")

    @application.post("/api/yomiage/stop")
    async def stop(request: Request):
        engine = _runtime(request).engine
        if engine and engine.is_running:
            engine.stop()
            return {"status": "stopped"}
        raise HTTPException(409, "Not reading")

    @application.post("/api/yomiage/synthesize")
    async def synthesize(req: SynthesizeRequest, request: Request):
        runtime = _runtime(request)
        engine = runtime.engine
        if engine is None:
            raise HTTPException(503, "Engine not initialized")
        lease = await _reserve_text_job(runtime, request, req.text)
        from .tts.base import TTSParams

        params = TTSParams(
            voice_id=req.voice_id,
            speed=req.speed,
            pitch=req.pitch,
            volume=req.volume,
            intonation=req.intonation,
            preset=req.preset,
            emotion=req.emotion,
            intensity=req.intensity,
        )
        try:
            result = await engine.tts.synthesize_immediate(req.text, params)
            if req.output_format != "wav":
                result = result.convert(req.output_format)
            return StreamingResponse(
                io.BytesIO(result.audio_data),
                media_type=f"audio/{req.output_format}",
                headers={
                    "X-Audio-Duration": str(result.duration or 0.0),
                    "X-Audio-Format": result.format,
                },
            )
        finally:
            if runtime.admission is not None:
                await runtime.admission.release(lease)

    @application.get("/api/yomiage/voices")
    async def list_voices(request: Request, provider: str | None = Query(None)):
        engine = _runtime(request).engine
        if engine is None:
            raise HTTPException(503, "Engine not initialized")
        tts = engine.tts
        if provider and tts.fallback and provider == tts.fallback.name:
            target = tts.fallback
        else:
            target = tts.primary
        voices = await target.list_voices()
        return {
            "engine": target.name,
            "voices": [
                {"id": str(v.get("id", "")), "name": v.get("name", v.get("label", ""))}
                for v in voices
            ],
        }

    @application.post("/api/yomiage/synthesize/batch")
    async def synthesize_batch(req: BatchSynthesizeRequest, request: Request):
        runtime = _runtime(request)
        manager = runtime.job_manager
        if manager is None:
            raise HTTPException(503, "Job manager not initialized")
        target = await _validate_url(runtime, req.url)
        lease = await _reserve_job(runtime, request)
        try:
            job = manager.create_job(
                url=target.url,
                mode=req.mode,
                output_format=req.output_format,
                video=req.video,
                style=req.style,
                allow_partial=req.allow_partial,
            )
        except BaseException:
            if runtime.admission is not None:
                await runtime.admission.release(lease)
            raise
        core_job = Job(
            id=job.id,
            options={
                "kind": "batch",
                "mode": req.mode,
                "output_format": req.output_format,
                "video": req.video,
                "allow_partial": req.allow_partial,
            },
        )

        async def operation(_job: Job) -> dict[str, Any]:
            await manager.run_job(
                job.id,
                output_format=req.output_format,
                video=req.video,
                style=req.style,
                allow_partial=req.allow_partial,
            )
            final = manager.get_job(job.id)
            return {
                "status": final.status if final else "completed",
                "error": final.error if final else None,
            }

        try:
            await _submit_background_job(runtime, core_job, operation, lease=lease)
        except BaseException:
            if runtime.admission is not None:
                await runtime.admission.release(lease)
            # Admission was checked before creating the in-memory BatchJob.  Remove it if a
            # concurrent submit still loses a race with the finite supervisor queue.
            jobs = getattr(manager, "_jobs", None)
            if isinstance(jobs, dict):
                jobs.pop(job.id, None)
            raise
        return {"job_id": job.id, "status": job.status}

    @application.get("/api/yomiage/synthesize/batch/{job_id}")
    async def get_batch_job(job_id: str, request: Request):
        manager = _runtime(request).job_manager
        if manager is None:
            raise HTTPException(503, "Job manager not initialized")
        job = manager.get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @application.get("/api/yomiage/synthesize/batch/{job_id}/progress")
    async def batch_job_progress(job_id: str, request: Request):
        manager = _runtime(request).job_manager
        if manager is None:
            raise HTTPException(503, "Job manager not initialized")

        async def event_stream():
            queue = manager.subscribe(job_id)
            try:
                while True:
                    event = await queue.get()
                    yield f"event: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event["status"] in (
                        "completed",
                        "complete_with_warnings",
                        "partial",
                        "failed",
                        "cancelled",
                    ):
                        break
            finally:
                manager.unsubscribe(job_id, queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @application.get("/api/yomiage/metrics")
    async def get_metrics(request: Request):
        runtime = _runtime(request)
        metrics = runtime.metrics
        if metrics is None:
            raise HTTPException(503, "Metrics not initialized")
        active, queued = _supervisor_counts(runtime)
        metrics.set_queue_depth(queued, active_jobs=active)
        payload = metrics.to_dict()
        # Persistent job counts intentionally remain separate from process
        # counters: a restart clears latency/counters but not this repository.
        repository = runtime.repository
        if repository is not None:
            try:
                listed = repository.list()
                if inspect.isawaitable(listed):
                    listed = await listed
                by_state: dict[str, int] = {}
                for item in listed or ():
                    state = getattr(item, "state", None)
                    state = getattr(state, "value", state)
                    key = str(state or "unknown")
                    by_state[key] = by_state.get(key, 0) + 1
                payload["persistent_jobs"] = {
                    "total": sum(by_state.values()),
                    "by_state": by_state,
                }
            except Exception:
                payload["persistent_jobs"] = {"total": None, "by_state": {}}
        return payload

    @application.post("/api/yomiage/news")
    async def news_daily(request: Request):
        runtime = _runtime(request)
        engine = runtime.engine
        if engine is None:
            raise HTTPException(503, "Engine not initialized")
        from .news.fetcher import NewsFetcher
        from .news.summarizer import NewsSummarizer

        ollama_cfg = runtime.config.get("ollama", {})
        if not isinstance(ollama_cfg, Mapping):
            ollama_cfg = {}
        ollama = OllamaClient(
            url=str(ollama_cfg.get("url", "http://localhost:11434")),
            model=str(ollama_cfg.get("summary_model") or "gemma4:12b"),
        )
        news_cfg = runtime.config.get("news", {})
        if not isinstance(news_cfg, Mapping):
            news_cfg = {}
        fetcher = NewsFetcher(news_cfg.get("sources"))
        summarizer = NewsSummarizer(ollama)
        articles = await fetcher.fetch_all()
        summary = await summarizer.daily_summary(articles)
        from .tts.base import TTSParams

        await engine.tts.start()
        await engine.tts.enqueue(summary, TTSParams())
        await engine.tts.drain()
        return {"status": "reading", "article_count": len(articles)}

    return application


# Legacy import path.  Its factories intentionally preserve monkeypatch hooks
# used by existing integrations; new apps should call create_app directly.
app = create_app(dependencies=_legacy_dependencies())


__all__ = [
    "BatchSynthesizeRequest",
    "ReadRequest",
    "ServerDependencies",
    "SynthesizeRequest",
    "app",
    "create_app",
    "lifespan",
]


# Kept as a named alias for callers that imported the old symbol.  Factory apps
# use their own closure; this alias only constructs the legacy app lifecycle.
lifespan = app.router.lifespan_context
