"""Application health checks used by the HTTP liveness/readiness endpoints.

The health use case deliberately knows only about small, duck-typed dependency
contracts.  This keeps it useful in the composition root and makes tests able to
provide deterministic fakes without constructing a real TTS or LLM client.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ports.tts import ProviderHealth

CHECK_NAMES = ("provider", "llm", "ffmpeg", "storage", "sqlite")
_SAFE_STATUSES = frozenset(
    {
        "healthy",
        "unhealthy",
        "ready",
        "not_ready",
        "available",
        "unavailable",
        "degraded",
        "not_configured",
        "invalid_result",
        "check_failed",
        "unwritable",
        "unknown",
    }
)


def _safe_label(value: Any, *, fallback: str = "component") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "")).strip("_")[:64]
    return normalized or fallback


def _safe_details(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep bounded topology booleans/counts while dropping arbitrary text."""

    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        label = _safe_label(key)
        if isinstance(item, bool):
            sanitized[label] = item
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            sanitized[label] = item
        elif isinstance(item, Mapping):
            sanitized[label] = _safe_details(item)
    return sanitized


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One dependency's secret-free health result."""

    healthy: bool
    status: str = "unknown"
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "healthy", bool(self.healthy))
        status = str(self.status).lower()
        object.__setattr__(
            self,
            "status",
            status if status in _SAFE_STATUSES else "unknown",
        )
        # Details are intentionally copied.  Callers cannot mutate a report after
        # it has been returned from a request handler.
        object.__setattr__(self, "details", _safe_details(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "status": self.status,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Readiness result with a fixed component set."""

    ready: bool
    checks: Mapping[str, HealthCheck]

    def __post_init__(self) -> None:
        normalized = {
            name: value if isinstance(value, HealthCheck) else _coerce_check(value)
            for name, value in self.checks.items()
        }
        # A fixed set makes dashboards and probes stable even when a dependency
        # is not configured.  Missing components are not silently omitted.
        for name in CHECK_NAMES:
            normalized.setdefault(name, HealthCheck(False, "not_configured"))
        object.__setattr__(self, "checks", normalized)
        object.__setattr__(
            self,
            "ready",
            bool(self.ready) and all(normalized[name].healthy for name in CHECK_NAMES),
        )

    @property
    def status(self) -> str:
        return "ready" if self.ready else "not_ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ready": self.ready,
            "checks": {name: self.checks[name].to_dict() for name in CHECK_NAMES},
        }


@dataclass(slots=True)
class HealthDependencies:
    """Optional fake/adapter dependencies for :class:`HealthChecker`.

    ``provider`` may be a single provider, a ``SynthesisService``-like object,
    or a mapping/sequence of providers.  Each other field may be a concrete
    object, a path, a boolean, or a sync/async checker callable.
    """

    provider: Any | None = None
    providers: Mapping[str, Any] | Sequence[Any] | None = None
    llm: Any | None = None
    ffmpeg: Any | None = None
    storage: Any | None = None
    sqlite: Any | None = None


def _coerce_check(value: Any) -> HealthCheck:
    if _is_mock(value):
        return HealthCheck(False, "invalid_result")
    if isinstance(value, HealthCheck):
        return value
    if isinstance(value, ProviderHealth):
        return HealthCheck(value.healthy, value.status)
    if isinstance(value, Mapping):
        healthy = value.get("healthy", value.get("ready", value.get("ok", False)))
        status = value.get("status", "healthy" if healthy else "unhealthy")
        details = value.get("details", {})
        return HealthCheck(
            bool(healthy),
            str(status),
            details if isinstance(details, Mapping) else {},
        )
    if isinstance(value, bool):
        return HealthCheck(value, "healthy" if value else "unhealthy")
    healthy = getattr(value, "healthy", getattr(value, "available", None))
    if healthy is not None:
        return HealthCheck(bool(healthy), "healthy" if healthy else "unhealthy")
    return HealthCheck(False, "invalid_result")


def _is_mock(value: Any) -> bool:
    """Avoid treating an unconfigured unittest MagicMock as a healthy service."""

    return type(value).__module__.startswith("unittest.mock")


async def _call(value: Any) -> Any:
    if callable(value):
        value = value()
    if inspect.isawaitable(value):
        return await value
    return value


def _failure() -> HealthCheck:
    # Do not return exception messages: provider errors often contain URLs,
    # credentials, or user input.  The status remains useful for operators.
    return HealthCheck(False, "check_failed")


def _path_value(value: Any) -> Path | None:
    if isinstance(value, (str, Path)):
        return Path(value).expanduser()
    return None


class HealthChecker:
    """Check process dependencies without coupling probes to FastAPI."""

    def __init__(
        self,
        *,
        engine: Any | None = None,
        repository: Any | None = None,
        config: Mapping[str, Any] | None = None,
        dependencies: HealthDependencies | None = None,
        provider: Any | None = None,
        providers: Mapping[str, Any] | Sequence[Any] | None = None,
        llm: Any | None = None,
        ffmpeg: Any | None = None,
        storage: Any | None = None,
        sqlite: Any | None = None,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.config = dict(config or {})
        if isinstance(dependencies, Mapping):
            supplied = HealthDependencies(**dict(dependencies))
        elif dependencies is None:
            supplied = HealthDependencies()
        else:
            supplied = dependencies
        self.provider = provider if provider is not None else supplied.provider
        self.providers = providers if providers is not None else supplied.providers
        self.llm = llm if llm is not None else supplied.llm
        self.ffmpeg = ffmpeg if ffmpeg is not None else supplied.ffmpeg
        self.storage = storage if storage is not None else supplied.storage
        self.sqlite = sqlite if sqlite is not None else supplied.sqlite

    async def liveness(self) -> dict[str, Any]:
        """Return process liveness without touching an external dependency."""

        return {"status": "ok", "live": True}

    async def readiness(self) -> HealthReport:
        checks = {
            "provider": await self._provider_check(),
            "llm": await self._llm_check(),
            "ffmpeg": await self._ffmpeg_check(),
            "storage": await self._storage_check(),
            "sqlite": await self._sqlite_check(),
        }
        return HealthReport(True, checks)

    # A short alias is convenient for fakes and for callers that use the probe
    # terminology instead of the HTTP terminology.
    async def live(self) -> dict[str, Any]:
        return await self.liveness()

    async def ready(self) -> HealthReport:
        return await self.readiness()

    def _provider_candidates(self) -> dict[str, Any]:
        source = self.providers
        if source is None:
            source = self.provider
        if source is None and self.engine is not None:
            service = getattr(self.engine, "synthesis_service", None)
            if service is not None and not _is_mock(service):
                source = service
            else:
                manager = getattr(self.engine, "tts", None)
                if manager is not None and not _is_mock(manager):
                    source = {
                        "primary": getattr(manager, "primary", None),
                        "fallback": getattr(manager, "fallback", None),
                    }
        if source is None:
            return {}
        if isinstance(source, Mapping):
            return {str(name): item for name, item in source.items() if item is not None}
        service_providers = getattr(source, "providers", None)
        if isinstance(service_providers, Mapping):
            return {str(name): item for name, item in service_providers.items()}
        if isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray)):
            return {str(getattr(item, "name", index)): item for index, item in enumerate(source)}
        return {str(getattr(source, "name", "primary")): source}

    async def _provider_check(self) -> HealthCheck:
        candidates = self._provider_candidates()
        if not candidates:
            return HealthCheck(False, "not_configured")
        statuses: dict[str, bool] = {}
        for name, provider in candidates.items():
            try:
                method = getattr(provider, "health", None)
                if not callable(method):
                    method = getattr(provider, "is_available", None)
                if callable(method):
                    value = await _call(method)
                elif hasattr(provider, "healthy"):
                    value = getattr(provider, "healthy")
                else:
                    value = False
                statuses[name] = _coerce_check(value).healthy
            except Exception:
                statuses[name] = False
        # A configured fallback keeps the service usable when primary is down.
        # The per-provider statuses remain visible without exposing provider errors.
        healthy = any(statuses.values())
        return HealthCheck(
            healthy,
            "healthy" if healthy else "unhealthy",
            {"providers": statuses},
        )

    def _llm_candidate(self) -> Any | None:
        if self.llm is not None:
            return self.llm
        if self.engine is not None:
            analyzer = getattr(self.engine, "scene_analyzer", None)
            if analyzer is not None and not _is_mock(analyzer):
                backend = getattr(analyzer, "_backend", None)
                if backend is not None and not _is_mock(backend):
                    return backend
                backend = getattr(analyzer, "ollama", None)
                if backend is not None and not _is_mock(backend):
                    return backend
        return None

    async def _llm_check(self) -> HealthCheck:
        candidate = self._llm_candidate()
        if candidate is None:
            return HealthCheck(False, "not_configured")
        try:
            method = getattr(candidate, "is_available", None)
            if not callable(method):
                method = getattr(candidate, "health", None)
            if not callable(method):
                return _coerce_check(candidate)
            return _coerce_check(await _call(method))
        except Exception:
            return _failure()

    async def _ffmpeg_check(self) -> HealthCheck:
        candidate = self.ffmpeg
        if candidate is None:
            storage = self.config.get("storage", {})
            if isinstance(storage, Mapping):
                candidate = storage.get("ffmpeg_executable") or storage.get("ffmpeg")
        try:
            if candidate is not None:
                if callable(candidate):
                    return _coerce_check(await _call(candidate))
                if isinstance(candidate, bool):
                    return _coerce_check(candidate)
                path = _path_value(candidate)
                if path is not None:
                    executable = path.is_file() and os.access(path, os.X_OK)
                    return HealthCheck(executable, "healthy" if executable else "unavailable")
                return _coerce_check(candidate)
            found = shutil.which("ffmpeg")
            return HealthCheck(bool(found), "healthy" if found else "unavailable")
        except Exception:
            return _failure()

    def _storage_paths(self) -> list[Path]:
        value = self.storage
        if value is None:
            value = self.config.get("storage", self.config.get("paths", {}))
        if isinstance(value, Mapping):
            values = [
                value.get("data_dir", value.get("data")),
                value.get("output_dir", value.get("output")),
            ]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values = list(value)
        else:
            values = [value]
        paths = [path for item in values if (path := _path_value(item)) is not None]
        if not paths:
            # Storage is still explicit in legacy raw config as top-level paths.
            for key in ("data_dir", "output_dir"):
                path = _path_value(self.config.get(key))
                if path is not None:
                    paths.append(path)
        return paths

    def _storage_entries(self) -> list[tuple[str, Path]]:
        """Resolve storage paths with stable labels (never expose path strings)."""

        value = self.storage
        if value is None:
            value = self.config.get("storage", self.config.get("paths", {}))
        if isinstance(value, Mapping):
            entries: list[tuple[str, Path]] = []
            for label, keys in (
                ("data", ("data_dir", "data")),
                ("output", ("output_dir", "output")),
            ):
                item = next((value.get(key) for key in keys if value.get(key) is not None), None)
                path = _path_value(item)
                if path is not None:
                    entries.append((label, path))
            return entries
        paths = self._storage_paths()
        return [(str(index), path) for index, path in enumerate(paths)]

    async def _storage_check(self) -> HealthCheck:
        value = self.storage
        if callable(value):
            try:
                return _coerce_check(await _call(value))
            except Exception:
                return _failure()
        entries = self._storage_entries()
        if not entries:
            return HealthCheck(False, "not_configured")
        statuses: dict[str, bool] = {}
        for label, path in entries:
            try:
                statuses[label] = path.is_dir() and os.access(path, os.W_OK | os.X_OK)
            except OSError:
                statuses[label] = False
        healthy = all(statuses.values())
        return HealthCheck(healthy, "healthy" if healthy else "unwritable", {"paths": statuses})

    async def _sqlite_check(self) -> HealthCheck:
        target = self.sqlite if self.sqlite is not None else self.repository
        if target is None:
            target = self.config.get("storage", {})
            if isinstance(target, Mapping):
                target = target.get("sqlite_path") or target.get("job_db_path")
        if target is None:
            return HealthCheck(False, "not_configured")
        try:
            if callable(target) and not hasattr(target, "execute"):
                return _coerce_check(await _call(target))
            connection = getattr(target, "_connection", target)
            execute = getattr(connection, "execute", None)
            if callable(execute):
                execute("SELECT 1")
                return HealthCheck(True, "healthy")
            path = _path_value(target)
            if path is not None:
                # Runtime construction normally creates the DB already.  Do not
                # create a new database from a probe; only verify its parent/path.
                exists = path.exists() and path.is_file()
                return HealthCheck(exists, "healthy" if exists else "unavailable")
            return _coerce_check(target)
        except (sqlite3.Error, OSError, RuntimeError):
            return _failure()
        except Exception:
            return _failure()


# Public aliases keep the use-case name discoverable without forcing callers to
# know the concrete checker terminology.
HealthUseCase = HealthChecker
HealthService = HealthChecker


__all__ = [
    "CHECK_NAMES",
    "HealthCheck",
    "HealthChecker",
    "HealthDependencies",
    "HealthReport",
    "HealthService",
    "HealthUseCase",
]
