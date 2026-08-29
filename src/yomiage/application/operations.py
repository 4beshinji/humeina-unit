"""外部operationのdeadlineとprocess所有権。

各呼出し層が同じtimeoutを再開始しないよう、monotonicな絶対時刻を共有する。
subprocessはこのmoduleだけがspawn後のterminate/kill/waitを所有する。
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class OperationDeadline:
    """monotonic clock上の絶対deadline。"""

    expires_at: float | None
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def after(
        cls,
        timeout: float | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> OperationDeadline:
        if timeout is None:
            return cls(None, clock)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return cls(clock() + float(timeout), clock)

    @classmethod
    def at(
        cls,
        expires_at: float | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> OperationDeadline:
        return cls(expires_at, clock)

    def remaining(self) -> float | None:
        if self.expires_at is None:
            return None
        return max(0.0, self.expires_at - self.clock())

    def expired(self) -> bool:
        remaining = self.remaining()
        return remaining is not None and remaining <= 0.0

    def child(self, max_duration: float | None) -> OperationDeadline:
        if max_duration is None:
            return self
        if max_duration <= 0:
            raise ValueError("max_duration must be positive")
        child_expiry = self.clock() + max_duration
        if self.expires_at is not None:
            child_expiry = min(child_expiry, self.expires_at)
        return OperationDeadline(child_expiry, self.clock)

    async def run(self, awaitable: Awaitable[T]) -> T:
        remaining = self.remaining()
        if remaining is None:
            return await awaitable
        if remaining <= 0:
            if hasattr(awaitable, "close"):
                awaitable.close()  # type: ignore[union-attr]
            raise TimeoutError("operation deadline exceeded")
        async with asyncio.timeout(remaining):
            return await awaitable

    async def sleep(self, delay: float) -> None:
        remaining = self.remaining()
        if remaining is not None:
            if remaining <= 0:
                raise TimeoutError("operation deadline exceeded")
            delay = min(delay, remaining)
        await asyncio.sleep(delay)
        if self.expired():
            raise TimeoutError("operation deadline exceeded")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """bounded captureを持つ外部process結果。"""

    returncode: int
    stdout: bytes
    stderr: bytes


class ExternalProcessRunner:
    """async subprocessを排他実行し、全終了経路で子をwaitする。"""

    def __init__(
        self,
        *,
        lock: asyncio.Lock | None = None,
        terminate_grace: float = 0.5,
        capture_limit: int = 1_000_000,
    ) -> None:
        if terminate_grace < 0:
            raise ValueError("terminate_grace must be non-negative")
        if capture_limit < 1:
            raise ValueError("capture_limit must be positive")
        self.lock = lock or asyncio.Lock()
        self.terminate_grace = terminate_grace
        self.capture_limit = capture_limit
        self._owned: set[Any] = set()

    @property
    def active_processes(self) -> int:
        return len(self._owned)

    async def run(
        self,
        command: Sequence[str],
        *,
        deadline: OperationDeadline | None = None,
        input_data: bytes | None = None,
        stdin: Any = None,
        stdout: Any = asyncio.subprocess.PIPE,
        stderr: Any = asyncio.subprocess.PIPE,
    ) -> ProcessResult:
        resolved_deadline = deadline or OperationDeadline.after(None)
        async with self.lock:
            process = await resolved_deadline.run(
                asyncio.create_subprocess_exec(
                    *command,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                )
            )
            self._owned.add(process)
            try:
                communication = (
                    process.communicate(input_data)
                    if input_data is not None
                    else process.communicate()
                )
                out, err = await resolved_deadline.run(communication)
                return ProcessResult(
                    int(process.returncode or 0),
                    bytes(out or b"")[-self.capture_limit :],
                    bytes(err or b"")[-self.capture_limit :],
                )
            except BaseException:
                await self._reap(process)
                raise
            finally:
                if process.returncode is None:
                    await self._reap(process)
                self._owned.discard(process)

    async def _reap(self, process: Any) -> None:
        if process.returncode is None:
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                terminate()
        try:
            if process.returncode is None:
                await asyncio.wait_for(process.wait(), timeout=self.terminate_grace)
        except (TimeoutError, ProcessLookupError):
            if process.returncode is None:
                kill = getattr(process, "kill", None)
                if callable(kill):
                    kill()
        finally:
            # wait() is idempotent for asyncio subprocesses and is required after kill.
            try:
                await process.wait()
            except (ProcessLookupError, AttributeError):
                return


class BlockingProcessCancelled(RuntimeError):
    """A worker-thread process operation was cancelled by its async owner."""


class ThreadProcessRunner:
    """Own ``Popen`` children executed inside one cancellable worker thread.

    Legacy ffmpeg composition remains synchronous, so its complete operation is
    moved to a worker.  This runner supplies the missing cooperative cancellation
    hook and does not return until terminate/kill and wait have completed.
    """

    def __init__(self, *, poll_interval: float = 0.05, terminate_grace: float = 0.5) -> None:
        self.poll_interval = poll_interval
        self.terminate_grace = terminate_grace
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._owned: set[subprocess.Popen[Any]] = set()

    @property
    def active_processes(self) -> int:
        with self._lock:
            return len(self._owned)

    def cancel(self) -> None:
        self._cancelled.set()

    def run(
        self,
        command: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        if self._cancelled.is_set():
            raise BlockingProcessCancelled("external process operation cancelled")
        popen_kwargs = dict(kwargs)
        if capture_output:
            if "stdout" in popen_kwargs or "stderr" in popen_kwargs:
                raise ValueError("stdout/stderr may not be used with capture_output")
            popen_kwargs["stdout"] = subprocess.PIPE
            popen_kwargs["stderr"] = subprocess.PIPE
        popen_kwargs["text"] = text
        process = subprocess.Popen(list(command), **popen_kwargs)
        with self._lock:
            self._owned.add(process)
        started = time.monotonic()
        try:
            while True:
                if self._cancelled.is_set():
                    self._terminate(process)
                    raise BlockingProcessCancelled("external process operation cancelled")
                remaining = None if timeout is None else timeout - (time.monotonic() - started)
                if remaining is not None and remaining <= 0:
                    self._terminate(process)
                    raise subprocess.TimeoutExpired(command, timeout)
                wait_slice = self.poll_interval
                if remaining is not None:
                    wait_slice = min(wait_slice, remaining)
                try:
                    stdout, stderr = process.communicate(timeout=wait_slice)
                    return subprocess.CompletedProcess(
                        list(command),
                        int(process.returncode or 0),
                        stdout,
                        stderr,
                    )
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                self._terminate(process)
            with self._lock:
                self._owned.discard(process)

    def _terminate(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=self.terminate_grace)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.communicate()


async def run_blocking_process_operation(
    operation: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run a legacy blocking operation with process cancellation ownership."""

    runner = ThreadProcessRunner()
    try:
        accepts_runner = "process_runner" in inspect.signature(operation).parameters
    except (TypeError, ValueError):
        accepts_runner = False
    if accepts_runner:
        kwargs["process_runner"] = runner
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        runner.cancel()
        try:
            await asyncio.shield(task)
        except (BlockingProcessCancelled, Exception):
            pass
        raise
    finally:
        if task.done() and not task.cancelled():
            # Consume a worker exception when cancellation raced with completion.
            task.exception()


__all__ = [
    "BlockingProcessCancelled",
    "ExternalProcessRunner",
    "OperationDeadline",
    "ProcessResult",
    "ThreadProcessRunner",
    "run_blocking_process_operation",
]
