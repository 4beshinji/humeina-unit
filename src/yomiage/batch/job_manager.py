"""Batch synthesis job manager with SSE progress notifications."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from ..api.hooks import EventHooks
from ..api.metrics import MetricsCollector
from ..application.operations import run_blocking_process_operation
from ..core.identity import new_job_id
from ..ports.sources import URLPolicy
from .engine import BatchEngine
from .manifest import BatchManifest


@dataclass
class BatchJob:
    """バッチ合成ジョブの状態."""

    id: str
    url: str
    mode: str
    status: str = "pending"
    percent: float = 0.0
    message: str = ""
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    updated_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    result: dict[str, Any] | None = None
    error: str | None = None
    output_path: str | None = None
    work_id: str | None = None
    plan_id: str | None = None
    run_id: str | None = None
    allow_partial: bool = False
    completion_status: str | None = None
    _queues: set[asyncio.Queue[dict]] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "mode": self.mode,
            "status": self.status,
            "percent": self.percent,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
            "output_path": self.output_path,
            "work_id": self.work_id,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "allow_partial": self.allow_partial,
            "completion_status": self.completion_status,
        }


class BatchJobManager:
    """バッチ合成ジョブを管理し、SSE で進捗を配信する."""

    def __init__(
        self,
        config: dict,
        output_dir: str | None = None,
        hooks: EventHooks | None = None,
        metrics: MetricsCollector | None = None,
        url_policy: URLPolicy | None = None,
    ):
        self.config = config
        from ..runtime_paths import output_dir as default_output_dir

        self.output_dir = str(Path(
            output_dir or config.get("batch", {}).get("output_dir") or default_output_dir()
        ).expanduser().resolve())
        self._jobs: dict[str, BatchJob] = {}
        self._hooks = hooks
        self._metrics = metrics
        self.url_policy = url_policy

    def create_job(
        self,
        url: str,
        mode: str = "voicevox",
        output_format: str = "wav",
        video: bool = False,
        style: str | None = None,
        allow_partial: bool = False,
    ) -> BatchJob:
        """新規ジョブを作成して返す."""
        job_id = new_job_id()
        job = BatchJob(
            id=job_id,
            url=url,
            mode=mode,
            status="pending",
            message="ジョブを作成しました",
            allow_partial=allow_partial,
        )
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> BatchJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[BatchJob]:
        return list(self._jobs.values())

    def subscribe(self, job_id: str) -> asyncio.Queue[dict]:
        """SSE 用の進捗キューを購読する."""
        job = self._get_or_raise(job_id)
        queue: asyncio.Queue[dict] = asyncio.Queue()
        job._queues.add(queue)
        # 接続直後に現在状態を送信
        queue.put_nowait(self._event(job, job.status, job.percent, job.message))
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[dict]) -> None:
        job = self._jobs.get(job_id)
        if job:
            job._queues.discard(queue)

    async def run_job(
        self,
        job_id: str,
        output_format: str = "wav",
        video: bool = False,
        style: str | None = None,
        cleanup: bool = False,
        allow_partial: bool | None = None,
    ) -> None:
        """ジョブを非同期で実行する."""
        job = self._get_or_raise(job_id)
        if allow_partial is None:
            allow_partial = job.allow_partial
        else:
            job.allow_partial = allow_partial
        engine = BatchEngine(
            self.config,
            mode=job.mode,
            output_dir=self.output_dir,
            output_format=output_format,
            cleanup=cleanup,
            url_policy=self.url_policy,
            metrics=self._metrics,
        )

        async def progress_callback(status: str, percent: float, message: str) -> None:
            await self._notify(job, status, percent, message)

        resources = AsyncExitStack()
        await resources.__aenter__()
        try:
            job_scope = getattr(getattr(engine, "llm", None), "job_scope", None)
            if callable(job_scope):
                await resources.enter_async_context(job_scope(job.id))
            await self._notify(job, "analyzing", 5.0, "テキストを分析中です")
            manifest = await engine.analyze(job.url)
            job.work_id = manifest.work_id
            job.plan_id = _string_identity(manifest, "plan_id")
            job.run_id = _string_identity(manifest, "run_id")
            # The manager's ID is the externally tracked job identity.  Analyzer-generated IDs are
            # retained for direct engine usage, but API jobs must remain correlated to this job.
            if hasattr(manifest, "job_id"):
                manifest.job_id = job.id
                if job.run_id and hasattr(manifest, "save"):
                    manifest.save(Path(self.output_dir))
            await self._notify(job, "analyzing", 30.0, "分析が完了しました")

            await self._notify(job, "synthesizing", 35.0, "音声を合成中です")
            synth_kwargs = {"progress_callback": progress_callback}
            if job.run_id:
                synth_kwargs["run_id"] = job.run_id
            manifest = await engine.synthesize(manifest.work_id, **synth_kwargs)
            # A mocked/legacy engine may return a fresh manifest object.  Keep the externally
            # assigned IDs on the job even when that object has no RUN-001 fields.
            job.plan_id = job.plan_id or _string_identity(manifest, "plan_id")
            job.run_id = job.run_id or _string_identity(manifest, "run_id")
            await self._notify(job, "synthesizing", 90.0, "音声合成が完了しました")

            completion_status = _manifest_completion_status(
                manifest, self.output_dir
            )
            job.completion_status = completion_status
            if completion_status in {"partial", "failed"} and not allow_partial:
                job.result = _job_result(manifest, None, completion_status)
                job.error = (
                    "音声合成が完全ではないため、部分成果物の結合を拒否しました"
                )
                await self._notify(job, "failed", 90.0, job.error)
                return
            if completion_status == "failed":
                job.result = _job_result(manifest, None, completion_status)
                job.error = "利用可能な音声成果物がありません"
                await self._notify(job, "failed", 90.0, job.error)
                return

            await self._notify(job, "concatenating", 92.0, "音声ファイルを結合中です")
            concat_kwargs = {"allow_partial": bool(allow_partial)}
            if job.run_id:
                concat_kwargs["run_id"] = job.run_id
            output_path = await run_blocking_process_operation(
                engine.concat,
                manifest.work_id,
                **concat_kwargs,
            )

            if output_path is None:
                job.result = _job_result(manifest, None, completion_status)
                job.error = "音声成果物を生成できませんでした"
                await self._notify(job, "failed", 92.0, job.error)
                return

            if video and completion_status != "partial":
                await self._notify(job, "concatenating", 95.0, "動画を生成中です")
                video_kwargs = {"style": style}
                if job.run_id:
                    video_kwargs["run_id"] = job.run_id
                video_path = await run_blocking_process_operation(
                    engine.video,
                    manifest.work_id,
                    **video_kwargs,
                )
                if video_path:
                    output_path = video_path

            final_path = str(output_path) if output_path else None
            job.output_path = final_path
            job.result = _job_result(manifest, final_path, completion_status)
            terminal_status = {
                "complete": "completed",
                "complete_with_warnings": "complete_with_warnings",
                "partial": "partial",
            }.get(completion_status, "failed")
            terminal_message = (
                "部分成果物で完了しました"
                if terminal_status == "partial"
                else "完了しました"
            )
            await self._notify(job, terminal_status, 100.0, terminal_message)
        except Exception as exc:  # pragma: no cover - エラーハンドリング網羅のため
            logger.exception(f"Batch job {job_id} failed")
            job.error = str(exc)
            await self._notify(job, "failed", job.percent, f"失敗しました: {exc}")
        finally:
            try:
                close = getattr(engine, "close", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            finally:
                await resources.aclose()

    async def _notify(
        self, job: BatchJob, status: str, percent: float, message: str
    ) -> None:
        job.status = status
        job.percent = percent
        job.message = message
        job.updated_at = asyncio.get_event_loop().time()

        event = self._event(job, status, percent, message)
        dead: set[asyncio.Queue[dict]] = set()
        for queue in job._queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead.add(queue)
        for queue in dead:
            job._queues.discard(queue)

        if self._hooks:
            self._hooks.emit_batch_job_status(
                job.id,
                status=status,
                percent=percent,
                message=message,
                output_path=job.output_path,
                error=job.error,
            )
        terminal_statuses = (
            "completed",
            "complete_with_warnings",
            "partial",
            "failed",
        )
        if self._metrics and status in terminal_statuses:
            self._metrics.record_batch_job(status, job_id=job.id)
        elif self._metrics:
            self._metrics.record_job_state(status, job_id=job.id)
        if self._metrics:
            active = sum(
                1
                for item in self._jobs.values()
                if item.status
                not in {
                    "completed",
                    "complete_with_warnings",
                    "partial",
                    "failed",
                    "cancelled",
                }
            )
            self._metrics.set_queue_depth(max(0, active - 1), active_jobs=active)

    def _event(self, job: BatchJob, status: str, percent: float, message: str) -> dict:
        return {
            "job_id": job.id,
            "status": status,
            "percent": percent,
            "message": message,
            "work_id": job.work_id,
            "plan_id": job.plan_id,
            "run_id": job.run_id,
            "output_path": job.output_path,
            "error": job.error,
            "completion_status": job.completion_status,
            "allow_partial": job.allow_partial,
        }

    def _get_or_raise(self, job_id: str) -> BatchJob:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Job not found: {job_id}")
        return job


def _manifest_completion_status(manifest: Any, output_dir: str) -> str:
    """実manifestの完全性を判定する（既存テストのmockも許容）."""
    if isinstance(manifest, BatchManifest):
        return manifest.refresh_completion_status(Path(output_dir))

    status = getattr(manifest, "completion_status", None)
    if isinstance(status, str) and status in {
        "complete",
        "complete_with_warnings",
        "partial",
        "failed",
    }:
        return status
    return "complete" if getattr(manifest, "synthesis_complete", True) is not False else "partial"


def _string_identity(manifest: Any, name: str) -> str | None:
    """Read identity fields from real manifests while tolerating old mocks/adapters."""

    value = getattr(manifest, name, None)
    return value if isinstance(value, str) and value else None


def _job_result(
    manifest: Any, output_path: str | None, completion_status: str
) -> dict[str, Any]:
    """APIへ返すバッチ成果物メタデータを統一する."""
    return {
        "work_id": manifest.work_id,
        "plan_id": _string_identity(manifest, "plan_id"),
        "run_id": _string_identity(manifest, "run_id"),
        "work_title": manifest.work_title,
        "chapters": len(manifest.chapters),
        "sentences": manifest.total_count,
        "output_path": output_path,
        "status": completion_status,
    }
