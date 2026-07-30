"""永続ジョブの状態機械と値オブジェクト。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class JobState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETE_WITH_WARNINGS = "complete_with_warnings"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class JobErrorCategory(str, Enum):
    VALIDATION = "validation"
    PROVIDER = "provider"
    STORAGE = "storage"
    CANCELLED = "cancelled"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


TERMINAL_JOB_STATES = frozenset(
    {
        JobState.COMPLETED,
        JobState.COMPLETE_WITH_WARNINGS,
        JobState.PARTIAL,
        JobState.FAILED,
        JobState.CANCELLED,
    }
)

_ALLOWED_TRANSITIONS = {
    JobState.PENDING: {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLED},
    JobState.QUEUED: {JobState.RUNNING, JobState.CANCELLED},
    JobState.RUNNING: {
        JobState.COMPLETED,
        JobState.COMPLETE_WITH_WARNINGS,
        JobState.PARTIAL,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.INTERRUPTED,
    },
    JobState.INTERRUPTED: {
        JobState.QUEUED,
        JobState.RUNNING,
        JobState.FAILED,
        JobState.CANCELLED,
    },
}

_SENSITIVE_OPTION_PARTS = ("secret", "token", "password", "api_key", "apikey")


class InvalidJobTransition(ValueError):
    """許可されていない状態遷移。"""


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(UTC)
    if not isinstance(result, datetime):
        raise TypeError("job timestamps must be datetime values")
    if result.tzinfo is None:
        raise ValueError("job timestamps must be timezone-aware")
    return result.astimezone(UTC)


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string or None")
    return value.strip()


def _safe_message(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = " ".join(value.split())
    if "://" in normalized:
        raise ValueError(f"{name} must not contain a complete URL")
    return normalized


def _safe_options(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("options must be a mapping")

    def validate(item: object, path: str) -> object:
        if isinstance(item, Mapping):
            result: dict[str, object] = {}
            for raw_key, child in item.items():
                key = str(raw_key)
                lowered = key.lower()
                sensitive_payload = lowered in {"text", "body", "content"} or lowered.endswith(
                    ("_text", "_body", "_content")
                )
                if sensitive_payload or any(
                    part in lowered for part in _SENSITIVE_OPTION_PARTS
                ):
                    raise ValueError(f"options must not persist sensitive field: {path}{key}")
                result[key] = validate(child, f"{path}{key}.")
            return result
        if isinstance(item, (list, tuple)):
            return [validate(child, path) for child in item]
        if item is None or isinstance(item, (bool, int, float, str)):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("options numbers must be finite")
            if isinstance(item, str) and "://" in item:
                raise ValueError("options must not persist complete URLs")
            return item
        raise TypeError(f"unsupported options value: {type(item).__name__}")

    return MappingProxyType(validate(value, ""))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class JobProgress:
    percent: float = 0.0
    message: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.percent, bool) or not isinstance(self.percent, (int, float)):
            raise TypeError("progress percent must be a number")
        percent = float(self.percent)
        if not math.isfinite(percent) or not 0.0 <= percent <= 100.0:
            raise ValueError("progress percent must be between 0 and 100")
        object.__setattr__(self, "percent", percent)
        object.__setattr__(self, "message", _safe_message(self.message, "progress message"))


@dataclass(frozen=True, slots=True)
class JobError:
    category: JobErrorCategory
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", JobErrorCategory(self.category))
        object.__setattr__(self, "message", _safe_message(self.message, "error message"))


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    state: JobState = JobState.PENDING
    progress: JobProgress = field(default_factory=JobProgress)
    options: Mapping[str, Any] = field(default_factory=dict)
    work_id: str | None = None
    plan_id: str | None = None
    run_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_category: JobErrorCategory | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("job id must not be empty")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("job revision must be a non-negative integer")
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "state", JobState(self.state))
        if not isinstance(self.progress, JobProgress):
            raise TypeError("progress must be JobProgress")
        object.__setattr__(self, "options", _safe_options(self.options))
        for name in ("work_id", "plan_id", "run_id"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, _utc(getattr(self, name)))
        for name in ("started_at", "finished_at"):
            value = getattr(self, name)
            object.__setattr__(self, name, _utc(value) if value is not None else None)
        if self.error_category is not None:
            object.__setattr__(self, "error_category", JobErrorCategory(self.error_category))
        if self.error_message is not None:
            object.__setattr__(
                self, "error_message", _safe_message(self.error_message, "error message")
            )


def transition_job(
    job: Job,
    target: JobState | str,
    *,
    progress: JobProgress | None = None,
    error: JobError | None = None,
    now: datetime | None = None,
) -> Job:
    """Jobを検証済みの次状態へ遷移した新しい値として返す。"""

    if not isinstance(job, Job):
        raise TypeError("job must be Job")
    target_state = JobState(target)
    if target_state not in _ALLOWED_TRANSITIONS.get(job.state, set()):
        raise InvalidJobTransition(
            f"transition not allowed: {job.state.value} -> {target_state.value}"
        )
    timestamp = _utc(now)
    resolved_progress = progress or job.progress
    if target_state in {JobState.COMPLETED, JobState.COMPLETE_WITH_WARNINGS} and progress is None:
        resolved_progress = JobProgress(100.0, job.progress.message)
    started_at = job.started_at
    if target_state is JobState.RUNNING and started_at is None:
        started_at = timestamp
    finished_at = timestamp if target_state in TERMINAL_JOB_STATES else None
    return replace(
        job,
        state=target_state,
        progress=resolved_progress,
        updated_at=timestamp,
        started_at=started_at,
        finished_at=finished_at,
        error_category=error.category if error else None,
        error_message=error.message if error else None,
        revision=job.revision + 1,
    )


__all__ = [
    "InvalidJobTransition",
    "Job",
    "JobError",
    "JobErrorCategory",
    "JobProgress",
    "JobState",
    "TERMINAL_JOB_STATES",
    "transition_job",
]
