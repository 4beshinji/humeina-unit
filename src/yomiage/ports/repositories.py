"""永続repositoryのapplication向けport。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from ..core.jobs import Job, JobError, JobProgress, JobState


class JobRepositoryError(RuntimeError):
    """JobRepositoryの基底例外。"""


class JobNotFoundError(JobRepositoryError):
    """指定jobが存在しない。"""


class JobConflictError(JobRepositoryError):
    """compare-and-setの期待状態またはrevisionが一致しない。"""


class JobRepository(Protocol):
    def create(self, job: Job) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def list(
        self,
        *,
        states: Sequence[JobState] | None = None,
        limit: int | None = None,
    ) -> list[Job]: ...

    def transition(
        self,
        job_id: str,
        target: JobState,
        *,
        expected_state: JobState,
        expected_revision: int,
        progress: JobProgress | None = None,
        error: JobError | None = None,
        now: datetime | None = None,
    ) -> Job: ...

    def request_cancel(self, job_id: str, *, expected_revision: int) -> Job: ...

    def close(self) -> None: ...


__all__ = [
    "JobConflictError",
    "JobNotFoundError",
    "JobRepository",
    "JobRepositoryError",
]
