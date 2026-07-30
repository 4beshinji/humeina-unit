"""JOB-001 state machine and SQLite repository contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yomiage.adapters.persistence.sqlite import SQLiteJobRepository
from yomiage.core.jobs import (
    InvalidJobTransition,
    Job,
    JobProgress,
    JobState,
    transition_job,
)
from yomiage.ports.repositories import JobConflictError


def _job(job_id: str = "job-1") -> Job:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    return Job(
        id=job_id,
        options={"provider": "voicevox", "output_format": "wav"},
        created_at=now,
        updated_at=now,
    )


def test_state_machine_rejects_invalid_transition_and_sets_utc_times() -> None:
    pending = _job()
    running = transition_job(
        pending,
        JobState.RUNNING,
        progress=JobProgress(10, "starting"),
        now=datetime(2026, 7, 30, 1, tzinfo=UTC),
    )
    completed = transition_job(
        running,
        JobState.COMPLETED,
        now=datetime(2026, 7, 30, 2, tzinfo=UTC),
    )

    assert running.started_at == datetime(2026, 7, 30, 1, tzinfo=UTC)
    assert completed.finished_at == datetime(2026, 7, 30, 2, tzinfo=UTC)
    assert completed.progress.percent == 100
    assert completed.revision == 2
    with pytest.raises(InvalidJobTransition):
        transition_job(completed, JobState.RUNNING)


def test_repository_reopen_persists_job_and_cancel_request(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    repository = SQLiteJobRepository(path)
    repository.create(_job())
    cancelled = repository.request_cancel("job-1", expected_revision=0)
    assert cancelled.cancel_requested is True
    assert cancelled.revision == 1
    repository.close()

    reopened = SQLiteJobRepository(path)
    loaded = reopened.get("job-1")
    assert loaded == cancelled
    assert reopened.list(states=[JobState.PENDING]) == [cancelled]
    reopened.close()


def test_two_repositories_cas_prevents_lost_update(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    first = SQLiteJobRepository(path)
    second = SQLiteJobRepository(path)
    first.create(_job())

    def run(repository: SQLiteJobRepository, target: JobState) -> object:
        try:
            return repository.transition(
                "job-1",
                target,
                expected_state=JobState.PENDING,
                expected_revision=0,
            )
        except JobConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: run(*item),
                [(first, JobState.QUEUED), (second, JobState.RUNNING)],
            )
        )

    assert sum(isinstance(item, Job) for item in results) == 1
    assert sum(isinstance(item, JobConflictError) for item in results) == 1
    assert first.get("job-1").revision == 1
    first.close()
    second.close()


@pytest.mark.parametrize(
    "options",
    [
        {"source_url": "https://example.test/private"},
        {"api_token": "secret"},
        {"nested": {"text": "novel body"}},
    ],
)
def test_job_rejects_sensitive_or_full_url_options(options: dict) -> None:
    with pytest.raises(ValueError, match="must not persist"):
        Job(id="unsafe", options=options)


def test_progress_and_error_messages_reject_complete_urls() -> None:
    with pytest.raises(ValueError, match="complete URL"):
        JobProgress(10, "fetching https://example.test/private")
