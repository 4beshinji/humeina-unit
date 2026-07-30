"""永続Jobを実行するTaskSupervisor。

このモジュールは、application層から ``asyncio.create_task`` を直接ばらまかないための
小さな実行基盤を提供する。受付済みのJob、worker、実行taskはすべてSupervisorが所有し、
終了時に例外を回収してから戻る。

ProviderやHTTP clientのライフサイクルは実行callbackが ``try/finally`` で所有する。
SupervisorはcallbackへCancellationを伝播し、callbackの後始末が完了するまでtaskを回収する。
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

from ..core.jobs import (
    TERMINAL_JOB_STATES,
    Job,
    JobError,
    JobErrorCategory,
    JobProgress,
    JobState,
)
from ..ports.repositories import JobConflictError, JobNotFoundError, JobRepository


class TaskSupervisorError(RuntimeError):
    """TaskSupervisorの基底例外。"""


class SupervisorClosedError(TaskSupervisorError):
    """停止済みSupervisorへの受付を拒否した。"""


class TaskQueueFullError(TaskSupervisorError):
    """有限キューが満杯でJobを受け付けられない。"""


class JobAlreadyActiveError(TaskSupervisorError):
    """同じJobを二重投入しようとした。"""


TaskOperation: TypeAlias = Callable[[Job], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class _QueuedTask:
    job: Job
    operation: TaskOperation


_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+"
)


def _safe_error_message(error: BaseException) -> str:
    """例外文を永続可能な短い診断文へ変換する。

    JobErrorは完全URLを拒否するため、providerがURLを含む例外を返しても状態保存自体が
    失敗しないように先に置換する。本文・tokenを新たにJob DBへ残さないことも目的とする。
    """

    raw = " ".join(str(error).split())
    raw = _SECRET_PATTERN.sub(r"\1=<redacted>", raw)
    raw = _URL_PATTERN.sub("<redacted-url>", raw)
    if not raw:
        raw = type(error).__name__
    return raw[:500]


def _error_category(error: BaseException) -> JobErrorCategory:
    if isinstance(error, asyncio.CancelledError):
        return JobErrorCategory.CANCELLED
    raw = getattr(error, "category", None)
    if isinstance(raw, JobErrorCategory):
        return raw
    try:
        return JobErrorCategory(str(raw))
    except (TypeError, ValueError):
        return JobErrorCategory.UNKNOWN


def _result_state(result: Any) -> JobState:
    """callback結果から正常終了状態を読む。未指定ならcompleted。"""

    candidate: Any = None
    if isinstance(result, JobState):
        candidate = result
    elif isinstance(result, Mapping):
        candidate = result.get("state", result.get("status", result.get("completion_status")))
    else:
        candidate = getattr(result, "state", None)
        if candidate is None:
            candidate = getattr(result, "status", None)
        if candidate is None:
            candidate = getattr(result, "completion_status", None)
    if candidate is None:
        return JobState.COMPLETED
    if isinstance(candidate, str):
        aliases = {
            "complete": JobState.COMPLETED,
            "completed": JobState.COMPLETED,
            "complete_with_warnings": JobState.COMPLETE_WITH_WARNINGS,
            "partial": JobState.PARTIAL,
            "failed": JobState.FAILED,
            "cancelled": JobState.CANCELLED,
        }
        candidate = aliases.get(candidate, candidate)
    try:
        state = JobState(candidate)
    except (TypeError, ValueError):
        return JobState.COMPLETED
    if state not in {
        JobState.COMPLETED,
        JobState.COMPLETE_WITH_WARNINGS,
        JobState.PARTIAL,
        JobState.FAILED,
        JobState.CANCELLED,
    }:
        return JobState.COMPLETED
    return state


def _result_error(result: Any, state: JobState) -> JobError | None:
    """callbackが返したfailed/cancelled状態を永続可能なJobErrorへ変換する。"""

    if state not in {JobState.FAILED, JobState.CANCELLED}:
        return None
    category = (
        JobErrorCategory.UNKNOWN
        if state is JobState.FAILED
        else JobErrorCategory.CANCELLED
    )
    message = "job reported failure" if state is JobState.FAILED else "job reported cancellation"
    if isinstance(result, Mapping):
        raw_category = result.get("error_category")
        try:
            if raw_category is not None:
                category = JobErrorCategory(raw_category)
        except (TypeError, ValueError):
            pass
        raw_message = result.get("error", result.get("message"))
        if isinstance(raw_message, str) and raw_message.strip():
            message = _safe_error_message(RuntimeError(raw_message))
    return JobError(category, message)


class TaskSupervisor:
    """有限queueとactive task registryを持つ単一ホスト向けJob supervisor。

    ``operation`` はJobを受け取り、async結果を返すcallbackである。callbackはProvider、HTTP
    session、一時ファイルなどの所有リソースを ``finally`` で閉じる必要がある。Supervisor
    はそのcallback taskへcancelを送り、例外とtaskを必ず回収する。
    """

    def __init__(
        self,
        repository: JobRepository,
        *,
        operation: TaskOperation | None = None,
        handler: TaskOperation | None = None,
        max_concurrency: int = 1,
        queue_size: int = 64,
        shutdown_grace_period: float = 5.0,
    ) -> None:
        if operation is not None and handler is not None:
            raise ValueError("operation and handler are aliases; pass only one")
        if (
            not isinstance(max_concurrency, int)
            or isinstance(max_concurrency, bool)
            or max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be a positive integer")
        if not isinstance(queue_size, int) or isinstance(queue_size, bool) or queue_size < 1:
            raise ValueError("queue_size must be a positive integer")
        if isinstance(shutdown_grace_period, bool) or shutdown_grace_period < 0:
            raise ValueError("shutdown_grace_period must be non-negative")
        self.repository = repository
        self._default_operation = operation or handler
        self.max_concurrency = max_concurrency
        self.queue_size = queue_size
        self.shutdown_grace_period = float(shutdown_grace_period)
        self._queue: asyncio.Queue[_QueuedTask] = asyncio.Queue(maxsize=queue_size)
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._workers: set[asyncio.Task[Any]] = set()
        self._operations: dict[str, TaskOperation] = {}
        self._started = False
        self._accepting = False
        self._shutting_down = False
        self._shutdown_complete = False

    @property
    def accepting(self) -> bool:
        """新規Jobを受付可能か。"""

        return self._accepting and not self._shutting_down

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def active_tasks(self) -> Mapping[str, asyncio.Task[Any]]:
        """active task registryの読み取り専用view。"""

        return dict(self._active)

    @property
    def active_job_ids(self) -> frozenset[str]:
        return frozenset(self._active)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def active(self) -> Mapping[str, asyncio.Task[Any]]:
        """active task registryの互換別名。"""

        return self.active_tasks

    @property
    def pending_queue(self) -> asyncio.Queue[_QueuedTask]:
        """監督対象queue。外部からputせず、depth監視用途に限定する。"""

        return self._queue

    async def start(self) -> tuple[Job, ...]:
        """Supervisorを起動し、前回プロセスの実行中Jobを回収する。"""

        if self._shutdown_complete:
            raise SupervisorClosedError("TaskSupervisor has already been closed")
        if self._started:
            return ()
        recovered = await self.recover_interrupted()
        self._started = True
        self._accepting = True
        for index in range(self.max_concurrency):
            task = asyncio.create_task(self._worker_loop(), name=f"yomiage-job-worker-{index}")
            self._workers.add(task)
            task.add_done_callback(self._worker_done)
        return recovered

    async def recover_interrupted(self) -> tuple[Job, ...]:
        """再起動時に永続化されたRUNNING/QUEUED Jobを回収する。

        RUNNINGは再開判定可能な ``interrupted`` とし、payloadを永続化していないQUEUEDは
        再投入できないため ``cancelled`` として確定する。transitionの競合は別の起動主体が
       先に回収したものとして安全に無視する。
        """

        recovered: list[Job] = []
        states = (JobState.RUNNING, JobState.QUEUED)
        for job in self.repository.list(states=states):
            try:
                if job.state is JobState.RUNNING:
                    updated = self.repository.transition(
                        job.id,
                        JobState.INTERRUPTED,
                        expected_state=job.state,
                        expected_revision=job.revision,
                        error=JobError(
                            JobErrorCategory.INTERNAL,
                            "job interrupted by process restart",
                        ),
                    )
                else:
                    updated = self.repository.transition(
                        job.id,
                        JobState.CANCELLED,
                        expected_state=job.state,
                        expected_revision=job.revision,
                        error=JobError(
                            JobErrorCategory.CANCELLED,
                            "queued job was not recoverable after process restart",
                        ),
                    )
            except JobConflictError:
                continue
            recovered.append(updated)
        return tuple(recovered)

    async def submit(
        self,
        job: Job,
        operation: TaskOperation | None = None,
    ) -> Job:
        """Jobを永続化状態QUEUEDへ進め、有限queueへ投入する。"""

        if not isinstance(job, Job):
            raise TypeError("job must be Job")
        if not self.accepting:
            if not self._started:
                await self.start()
            else:
                raise SupervisorClosedError("TaskSupervisor is not accepting jobs")
        if self._queue.full():
            raise TaskQueueFullError("job queue is full")
        if job.id in self._active or job.id in self._operations:
            raise JobAlreadyActiveError(f"job already submitted: {job.id}")
        resolved = operation or self._default_operation
        if resolved is None or not callable(resolved):
            raise ValueError("an operation callback is required")
        existing = self.repository.get(job.id)
        if existing is None:
            self.repository.create(job)
            existing = job
        elif existing.state is not JobState.PENDING:
            raise JobAlreadyActiveError(
                f"job must be pending before submission: {job.id} ({existing.state.value})"
            )
        queued = self.repository.transition(
            existing.id,
            JobState.QUEUED,
            expected_state=existing.state,
            expected_revision=existing.revision,
        )
        self._operations[queued.id] = resolved
        try:
            self._queue.put_nowait(_QueuedTask(queued, resolved))
        except asyncio.QueueFull as exc:  # pragma: no cover - full() covers single-loop path
            self._operations.pop(queued.id, None)
            self._cancel_queued(queued, "job queue is full")
            raise TaskQueueFullError("job queue is full") from exc
        return queued

    enqueue = submit

    async def resume(
        self,
        job_id: str,
        operation: TaskOperation | None = None,
    ) -> Job:
        """再起動回収済みのINTERRUPTED Jobを明示的に再投入する。"""

        if not self.accepting:
            if not self._started:
                await self.start()
            else:
                raise SupervisorClosedError("TaskSupervisor is not accepting jobs")
        if self._queue.full():
            raise TaskQueueFullError("job queue is full")
        current = self.repository.get(job_id)
        if current is None:
            raise JobNotFoundError(f"job not found: {job_id}")
        if current.state is not JobState.INTERRUPTED:
            raise JobAlreadyActiveError(
                f"job must be interrupted before resume: {job_id} ({current.state.value})"
            )
        resolved = operation or self._default_operation
        if resolved is None or not callable(resolved):
            raise ValueError("an operation callback is required")
        queued = self.repository.transition(
            current.id,
            JobState.QUEUED,
            expected_state=current.state,
            expected_revision=current.revision,
        )
        self._operations[queued.id] = resolved
        try:
            self._queue.put_nowait(_QueuedTask(queued, resolved))
        except asyncio.QueueFull as exc:  # pragma: no cover - full() covers single-loop path
            self._operations.pop(queued.id, None)
            self._cancel_queued(queued, "job queue is full")
            raise TaskQueueFullError("job queue is full") from exc
        return queued

    resume_job = resume

    async def cancel(self, job_id: str) -> Job:
        """Jobのcancel requestを永続化し、active taskへCancellationを送る。"""

        current = self.repository.get(job_id)
        if current is None:
            raise JobNotFoundError(f"job not found: {job_id}")
        if current.state in TERMINAL_JOB_STATES:
            return current
        if current.cancel_requested:
            requested = current
        else:
            try:
                requested = self.repository.request_cancel(
                    current.id, expected_revision=current.revision
                )
            except JobConflictError:
                # The callback may have reached a terminal state between get() and the CAS.
                # Cancellation is idempotent from the caller's perspective in that case.
                latest = self.repository.get(current.id)
                if latest is None:
                    raise JobNotFoundError(f"job not found: {job_id}")
                if latest.state in TERMINAL_JOB_STATES:
                    return latest
                raise
        if requested.state in {JobState.PENDING, JobState.QUEUED}:
            return self._cancel_queued(requested, "job cancellation requested")
        if requested.state is JobState.RUNNING:
            task = self._active.get(requested.id)
            if task is not None and not task.done():
                task.cancel()
                # Cancellation is an API boundary: do not report before provider/session/temp
                # file cleanup in the callback's finally block has completed.
                await asyncio.gather(task, return_exceptions=True)
            return self.repository.get(requested.id) or requested
        return requested

    request_cancel = cancel
    cancel_job = cancel

    def is_cancel_requested(self, job_id: str) -> bool:
        job = self.repository.get(job_id)
        return bool(job and job.cancel_requested)

    async def shutdown(self, *, grace_period: float | None = None) -> None:
        """受付を止め、猶予後にactive taskをcancelして全taskを回収する。"""

        if self._shutdown_complete:
            return
        self._shutting_down = True
        self._accepting = False
        grace = self.shutdown_grace_period if grace_period is None else float(grace_period)
        if grace < 0:
            raise ValueError("grace_period must be non-negative")
        # queue内および、別instanceの起動で残ったQUEUEDを受け付けず確定させる。
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._cancel_queued(self.repository.get(item.job.id) or item.job, "supervisor shutdown")
            self._operations.pop(item.job.id, None)
            self._queue.task_done()
        for job in self.repository.list(states=(JobState.PENDING, JobState.QUEUED)):
            if job.id not in self._active:
                self._cancel_queued(job, "supervisor shutdown")

        active = tuple(self._active.values())
        if active:
            done, pending = await asyncio.wait(active, timeout=grace)
            del done
            if pending:
                # Persist the request before cancelling the callback. This lets a restarted
                # process distinguish an intentional shutdown from a crash and preserves the
                # cancellation bit even when the callback exits immediately on CancelledError.
                for job_id in tuple(self._active):
                    task = self._active.get(job_id)
                    if task not in pending:
                        continue
                    current = self.repository.get(job_id)
                    if (
                        current is not None
                        and current.state not in TERMINAL_JOB_STATES
                        and not current.cancel_requested
                    ):
                        try:
                            self.repository.request_cancel(
                                job_id, expected_revision=current.revision
                            )
                        except JobConflictError:
                            pass
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        # Worker tasks block on queue.get once active jobs have settled.
        workers = tuple(self._workers)
        for worker in workers:
            if not worker.done():
                worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()
        self._shutdown_complete = True
        self._started = False

    async def close(self) -> None:
        await self.shutdown()

    stop = shutdown
    recover_on_startup = recover_interrupted

    async def __aenter__(self) -> "TaskSupervisor":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.shutdown()

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            task = asyncio.create_task(
                self._execute(item), name=f"yomiage-job-{item.job.id}"
            )
            self._active[item.job.id] = task
            try:
                await task
            except asyncio.CancelledError:
                # shutdownがworkerをcancelした場合でもchild taskを回収する。
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except Exception:
                # _executeは永続化後に例外を吸収するが、予期せぬ監督側エラーも未回収にしない。
                await asyncio.gather(task, return_exceptions=True)
            finally:
                self._active.pop(item.job.id, None)
                self._operations.pop(item.job.id, None)
                self._queue.task_done()

    async def _execute(self, item: _QueuedTask) -> None:
        job = self.repository.get(item.job.id)
        if job is None:
            return
        if job.cancel_requested or job.state is JobState.CANCELLED:
            if job.state in {JobState.PENDING, JobState.QUEUED}:
                self._cancel_queued(job, "job cancellation requested")
            return
        try:
            running = self.repository.transition(
                job.id,
                JobState.RUNNING,
                expected_state=job.state,
                expected_revision=job.revision,
            )
            result = item.operation(running)
            if inspect.isawaitable(result):
                result = await result
            current = self.repository.get(job.id)
            if current is None or current.state in TERMINAL_JOB_STATES:
                return
            if current.cancel_requested:
                self._cancel_queued(current, "job cancellation requested")
                return
            target = _result_state(result)
            result_error = _result_error(result, target)
            progress = (
                JobProgress(100.0, current.progress.message)
                if target in {JobState.COMPLETED, JobState.COMPLETE_WITH_WARNINGS}
                else current.progress
            )
            self.repository.transition(
                job.id,
                target,
                expected_state=current.state,
                expected_revision=current.revision,
                progress=progress,
                error=result_error,
            )
        except asyncio.CancelledError as exc:
            self._mark_cancelled(job.id, exc)
        except Exception as exc:
            self._mark_failed(job.id, exc)

    def _cancel_queued(self, job: Job, message: str) -> Job:
        current = self.repository.get(job.id) or job
        if current.state in TERMINAL_JOB_STATES:
            return current
        if not current.cancel_requested:
            try:
                current = self.repository.request_cancel(
                    current.id, expected_revision=current.revision
                )
            except JobConflictError:
                current = self.repository.get(current.id) or current
        if current.state in {JobState.PENDING, JobState.QUEUED}:
            try:
                return self.repository.transition(
                    current.id,
                    JobState.CANCELLED,
                    expected_state=current.state,
                    expected_revision=current.revision,
                    error=JobError(JobErrorCategory.CANCELLED, message),
                )
            except JobConflictError:
                return self.repository.get(current.id) or current
        return current

    def _mark_cancelled(self, job_id: str, error: BaseException) -> Job | None:
        current = self.repository.get(job_id)
        if current is None or current.state in TERMINAL_JOB_STATES:
            return current
        if current.state not in {JobState.RUNNING, JobState.QUEUED, JobState.PENDING}:
            return current
        try:
            return self.repository.transition(
                job_id,
                JobState.CANCELLED,
                expected_state=current.state,
                expected_revision=current.revision,
                error=JobError(JobErrorCategory.CANCELLED, "job cancelled"),
            )
        except JobConflictError:
            return self.repository.get(job_id)

    def _mark_failed(self, job_id: str, error: BaseException) -> Job | None:
        current = self.repository.get(job_id)
        if current is None or current.state in TERMINAL_JOB_STATES:
            return current
        if current.cancel_requested:
            return self._mark_cancelled(job_id, error)
        try:
            return self.repository.transition(
                job_id,
                JobState.FAILED,
                expected_state=current.state,
                expected_revision=current.revision,
                error=JobError(_error_category(error), _safe_error_message(error)),
            )
        except JobConflictError:
            return self.repository.get(job_id)

    def _worker_done(self, task: asyncio.Task[Any]) -> None:
        self._workers.discard(task)
        # worker exceptions are observed here as well, so a callback bug cannot produce a
        # "Task exception was never retrieved" warning during interpreter shutdown.
        if not task.cancelled():
            task.exception()


class JobService:
    """RepositoryとTaskSupervisorを束ねる薄いapplication facade。"""

    def __init__(self, repository: JobRepository, supervisor: TaskSupervisor) -> None:
        self.repository = repository
        self.supervisor = supervisor

    def get(self, job_id: str) -> Job | None:
        return self.repository.get(job_id)

    def list(self, **kwargs: Any) -> list[Job]:
        return self.repository.list(**kwargs)

    async def submit(self, job: Job, operation: TaskOperation | None = None) -> Job:
        return await self.supervisor.submit(job, operation)

    async def cancel(self, job_id: str) -> Job:
        return await self.supervisor.cancel(job_id)

    async def resume(self, job_id: str, operation: TaskOperation | None = None) -> Job:
        return await self.supervisor.resume(job_id, operation)

    async def close(self) -> None:
        await self.supervisor.close()


__all__ = [
    "JobAlreadyActiveError",
    "JobService",
    "SupervisorClosedError",
    "TaskOperation",
    "TaskQueueFullError",
    "TaskSupervisor",
    "TaskSupervisorError",
]
