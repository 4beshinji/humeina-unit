"""FastAPI server and application factory.

The exported :data:`app` is kept as a legacy entry point, but the actual route
handlers only read the per-application runtime stored in ``app.state``.  New
callers should use :func:`create_app` and pass their settings/dependencies
explicitly; this makes two ASGI applications safe to host in one process.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from .adapters.persistence.sqlite import SQLiteJobRepository
from .api.hooks import EventHooks
from .api.metrics import MetricsCollector
from .application.jobs import TaskSupervisor
from .batch.job_manager import BatchJobManager
from .bootstrap import create_reading_engine
from .config import load_config
from .core.identity import new_job_id
from .core.jobs import Job
from .nlp.ollama_client import OllamaClient
from .ports.repositories import JobRepository

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
    repository: JobRepository | None = None
    repository_factory: Callable[..., Any] | None = None
    supervisor: TaskSupervisor | None = None
    supervisor_factory: Callable[..., Any] | None = None
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
    await _await_close(tts, "TTS worker")
    seen: set[int] = set()
    for provider in (getattr(tts, "primary", None), getattr(tts, "fallback", None)):
        if provider is None or id(provider) in seen:
            continue
        seen.add(id(provider))
        await _await_close(provider, "TTS provider")


async def _build_runtime(settings: Any, dependencies: ServerDependencies) -> _Runtime:
    config = await _resolve_settings(settings, dependencies)
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


async def _submit_background_job(runtime: _Runtime, job: Job, operation: Any) -> Job:
    if runtime.supervisor is not None:
        return await runtime.supervisor.submit(job, operation)
    task = asyncio.create_task(operation(job), name=f"yomiage-compat-job-{job.id}")
    runtime.compat_tasks.add(task)
    task.add_done_callback(runtime.compat_tasks.discard)
    return job


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
        application.state.job_repository = runtime.repository
        application.state.task_supervisor = runtime.supervisor
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
                "job_repository",
                "task_supervisor",
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

    @application.get("/health")
    async def health(request: Request):
        runtime = _runtime(request)
        engine = runtime.engine
        return {"status": "ok", "reading": bool(engine and engine.is_running)}

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
        job = Job(id=new_job_id(), options={"kind": "read"})

        async def operation(_job: Job) -> None:
            await engine.read_url(req.url)

        await _submit_background_job(runtime, job, operation)
        return {"status": "started", "url": req.url, "job_id": job.id}

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
        engine = _runtime(request).engine
        if engine is None:
            raise HTTPException(503, "Engine not initialized")
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
        job = manager.create_job(
            url=req.url,
            mode=req.mode,
            output_format=req.output_format,
            video=req.video,
            style=req.style,
            allow_partial=req.allow_partial,
        )
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

        await _submit_background_job(runtime, core_job, operation)
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
        metrics = _runtime(request).metrics
        if metrics is None:
            raise HTTPException(503, "Metrics not initialized")
        return metrics.to_dict()

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
            model=str(ollama_cfg.get("summary_model") or ollama_cfg.get("model", "qwen3.5:3b")),
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
