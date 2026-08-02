"""SQLiteによる単一ホスト向けJobRepository。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from ...core.jobs import (
    Job,
    JobError,
    JobErrorCategory,
    JobProgress,
    JobState,
    transition_job,
)
from ...ports.repositories import JobConflictError, JobNotFoundError

SCHEMA_VERSION = 1


class SQLiteJobRepository:
    """revision付きCASでlost updateを拒否するSQLite repository。"""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        if isinstance(busy_timeout_ms, bool) or busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            check_same_thread=False,
            timeout=busy_timeout_ms / 1000,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_metadata "
                    "(name TEXT PRIMARY KEY, version INTEGER NOT NULL)"
                )
                row = self._connection.execute(
                    "SELECT version FROM schema_metadata WHERE name = 'jobs'"
                ).fetchone()
                if row is not None and int(row["version"]) > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported jobs schema version: {row['version']}"
                    )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        progress_percent REAL NOT NULL,
                        progress_message TEXT NOT NULL,
                        options_json TEXT NOT NULL,
                        work_id TEXT,
                        plan_id TEXT,
                        run_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        error_category TEXT,
                        error_message TEXT,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        revision INTEGER NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_metadata(name, version) VALUES('jobs', ?) "
                    "ON CONFLICT(name) DO UPDATE SET version = excluded.version",
                    (SCHEMA_VERSION,),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def create(self, job: Job) -> Job:
        if not isinstance(job, Job):
            raise TypeError("job must be Job")
        values = self._job_values(job)
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO jobs (
                        job_id, state, progress_percent, progress_message, options_json,
                        work_id, plan_id, run_id, created_at, updated_at, started_at,
                        finished_at, error_category, error_message, cancel_requested, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise JobConflictError(f"job already exists: {job.id}") from exc
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list(
        self,
        *,
        states: Sequence[JobState] | None = None,
        limit: int | None = None,
    ) -> list[Job]:
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        ):
            raise ValueError("limit must be a positive integer")
        sql = "SELECT * FROM jobs"
        parameters: list[Any] = []
        if states:
            resolved = [JobState(state).value for state in states]
            sql += f" WHERE state IN ({','.join('?' for _ in resolved)})"
            parameters.extend(resolved)
        sql += " ORDER BY created_at, job_id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [self._row_to_job(row) for row in rows]

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
    ) -> Job:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
                ).fetchone()
                if row is None:
                    raise JobNotFoundError(f"job not found: {job_id}")
                current = self._row_to_job(row)
                if (
                    current.state is not JobState(expected_state)
                    or current.revision != expected_revision
                ):
                    raise JobConflictError(
                        f"job changed: {job_id} "
                        f"(state={current.state.value}, revision={current.revision})"
                    )
                updated = transition_job(
                    current, target, progress=progress, error=error, now=now
                )
                cursor = self._connection.execute(
                    """
                    UPDATE jobs SET
                        state=?, progress_percent=?, progress_message=?, options_json=?,
                        work_id=?, plan_id=?, run_id=?, created_at=?, updated_at=?,
                        started_at=?, finished_at=?, error_category=?, error_message=?,
                        cancel_requested=?, revision=?
                    WHERE job_id=? AND state=? AND revision=?
                    """,
                    (
                        *self._job_values(updated)[1:],
                        current.id,
                        current.state.value,
                        current.revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise JobConflictError(f"concurrent job transition: {job_id}")
                self._connection.execute("COMMIT")
                return updated
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def request_cancel(self, job_id: str, *, expected_revision: int) -> Job:
        timestamp = datetime.now(UTC).isoformat()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
                ).fetchone()
                if row is None:
                    raise JobNotFoundError(f"job not found: {job_id}")
                current = self._row_to_job(row)
                if current.revision != expected_revision:
                    raise JobConflictError(f"job changed: {job_id}")
                cursor = self._connection.execute(
                    """
                    UPDATE jobs
                    SET cancel_requested=1, revision=revision+1, updated_at=?
                    WHERE job_id=? AND revision=?
                    """,
                    (timestamp, current.id, current.revision),
                )
                if cursor.rowcount != 1:
                    raise JobConflictError(f"concurrent cancellation request: {job_id}")
                updated_row = self._connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (current.id,)
                ).fetchone()
                self._connection.execute("COMMIT")
                return self._row_to_job(updated_row)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _job_values(job: Job) -> tuple[Any, ...]:
        return (
            job.id,
            job.state.value,
            job.progress.percent,
            job.progress.message,
            json.dumps(dict(job.options), ensure_ascii=False, sort_keys=True, allow_nan=False),
            job.work_id,
            job.plan_id,
            job.run_id,
            job.created_at.isoformat(),
            job.updated_at.isoformat(),
            job.started_at.isoformat() if job.started_at else None,
            job.finished_at.isoformat() if job.finished_at else None,
            job.error_category.value if job.error_category else None,
            job.error_message,
            int(job.cancel_requested),
            job.revision,
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["job_id"],
            state=JobState(row["state"]),
            progress=JobProgress(row["progress_percent"], row["progress_message"]),
            options=json.loads(row["options_json"]),
            work_id=row["work_id"],
            plan_id=row["plan_id"],
            run_id=row["run_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
            ),
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
            error_category=(
                JobErrorCategory(row["error_category"]) if row["error_category"] else None
            ),
            error_message=row["error_message"],
            cancel_requested=bool(row["cancel_requested"]),
            revision=row["revision"],
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


__all__ = ["SCHEMA_VERSION", "SQLiteJobRepository"]
