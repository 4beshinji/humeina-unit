"""JOB-002 TaskSupervisorのcancel/shutdown/restart契約。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yomiage.adapters.persistence.sqlite import SQLiteJobRepository
from yomiage.application.jobs import (
    SupervisorClosedError,
    TaskQueueFullError,
    TaskSupervisor,
)
from yomiage.core.jobs import Job, JobErrorCategory, JobState


def _job(job_id: str) -> Job:
    return Job(id=job_id, options={"kind": "test"})


@pytest.mark.asyncio
async def test_success_and_failure_are_persisted_without_unretrieved_task_errors(
    tmp_path: Path,
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    supervisor = TaskSupervisor(repository, max_concurrency=2)
    finished: list[str] = []

    async def success(job: Job) -> None:
        finished.append(job.id)

    async def failure(job: Job) -> None:
        raise RuntimeError("provider failed")

    await supervisor.start()
    first = await supervisor.submit(_job("ok"), success)
    second = await supervisor.submit(_job("bad"), failure)
    assert first.state is JobState.QUEUED
    assert second.state is JobState.QUEUED
    await asyncio.wait_for(supervisor._queue.join(), timeout=1.0)
    assert repository.get("ok").state is JobState.COMPLETED
    failed = repository.get("bad")
    assert failed is not None
    assert failed.state is JobState.FAILED
    assert failed.progress.percent == 0.0
    assert failed.error_category is JobErrorCategory.UNKNOWN
    assert finished == ["ok"]
    await supervisor.close()
    repository.close()


@pytest.mark.asyncio
async def test_cancel_propagates_to_operation_and_runs_cleanup(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    supervisor = TaskSupervisor(repository, shutdown_grace_period=0)
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def operation(job: Job) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    await supervisor.start()
    await supervisor.submit(_job("cancel-me"), operation)
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await supervisor.cancel("cancel-me")
    await asyncio.wait_for(cleaned.wait(), timeout=1.0)
    await asyncio.wait_for(supervisor._queue.join(), timeout=1.0)
    cancelled = repository.get("cancel-me")
    assert cancelled is not None
    assert cancelled.state is JobState.CANCELLED
    assert cancelled.progress.percent == 0.0
    assert cancelled.cancel_requested is True
    await supervisor.close()
    repository.close()


@pytest.mark.asyncio
async def test_shutdown_stops_admission_drains_queue_and_cancels_active(
    tmp_path: Path,
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    supervisor = TaskSupervisor(
        repository,
        max_concurrency=1,
        queue_size=1,
        shutdown_grace_period=0,
    )
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def operation(job: Job) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    await supervisor.start()
    await supervisor.submit(_job("running"), operation)
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await supervisor.submit(_job("queued"), operation)
    await supervisor.shutdown()
    assert cleaned.is_set()
    assert supervisor.accepting is False
    running = repository.get("running")
    assert running.state is JobState.CANCELLED
    assert running.cancel_requested is True
    assert repository.get("queued").state is JobState.CANCELLED
    with pytest.raises(SupervisorClosedError):
        await supervisor.submit(_job("after-shutdown"), operation)
    repository.close()


@pytest.mark.asyncio
async def test_bounded_queue_rejects_before_persisting_an_extra_job(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    supervisor = TaskSupervisor(repository, queue_size=1)
    await supervisor.start()
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def operation(job: Job) -> None:
        entered.set()
        await gate.wait()

    await supervisor.submit(_job("first"), operation)
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await supervisor.submit(_job("second"), operation)
    with pytest.raises(TaskQueueFullError):
        await supervisor.submit(_job("third"), operation)
    assert repository.get("third") is None
    gate.set()
    await supervisor.shutdown()
    repository.close()


@pytest.mark.asyncio
async def test_restart_recovers_running_and_nonrecoverable_queued_jobs(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    repository = SQLiteJobRepository(path)
    repository.create(_job("running"))
    repository.transition(
        "running",
        JobState.RUNNING,
        expected_state=JobState.PENDING,
        expected_revision=0,
    )
    repository.create(_job("queued"))
    repository.transition(
        "queued",
        JobState.QUEUED,
        expected_state=JobState.PENDING,
        expected_revision=0,
    )
    repository.close()

    reopened = SQLiteJobRepository(path)
    supervisor = TaskSupervisor(reopened)
    recovered = await supervisor.start()
    assert {job.id for job in recovered} == {"running", "queued"}
    assert reopened.get("running").state is JobState.INTERRUPTED
    assert reopened.get("queued").state is JobState.CANCELLED
    await supervisor.resume("running", lambda _job: None)
    await asyncio.wait_for(supervisor._queue.join(), timeout=1.0)
    assert reopened.get("running").state is JobState.COMPLETED
    await supervisor.close()
    reopened.close()
