"""Batch synthesis job manager with SSE progress notifications."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from .engine import BatchEngine


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
        }


class BatchJobManager:
    """バッチ合成ジョブを管理し、SSE で進捗を配信する."""

    def __init__(self, config: dict, output_dir: str = "output"):
        self.config = config
        self.output_dir = output_dir
        self._jobs: dict[str, BatchJob] = {}

    def create_job(
        self,
        url: str,
        mode: str = "voicevox",
        output_format: str = "wav",
        video: bool = False,
        style: str | None = None,
    ) -> BatchJob:
        """新規ジョブを作成して返す."""
        job_id = uuid.uuid4().hex[:12]
        job = BatchJob(
            id=job_id,
            url=url,
            mode=mode,
            status="pending",
            message="ジョブを作成しました",
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
    ) -> None:
        """ジョブを非同期で実行する."""
        job = self._get_or_raise(job_id)
        engine = BatchEngine(
            self.config,
            mode=job.mode,
            output_dir=self.output_dir,
            output_format=output_format,
            cleanup=cleanup,
        )

        async def progress_callback(status: str, percent: float, message: str) -> None:
            await self._notify(job, status, percent, message)

        try:
            await self._notify(job, "analyzing", 5.0, "テキストを分析中です")
            manifest = await engine.analyze(job.url)
            job.work_id = manifest.work_id
            await self._notify(job, "analyzing", 30.0, "分析が完了しました")

            await self._notify(job, "synthesizing", 35.0, "音声を合成中です")
            await engine.synthesize(
                manifest.work_id,
                progress_callback=progress_callback,
            )
            await self._notify(job, "synthesizing", 90.0, "音声合成が完了しました")

            await self._notify(job, "concatenating", 92.0, "音声ファイルを結合中です")
            output_path = engine.concat(manifest.work_id)

            if video:
                await self._notify(job, "concatenating", 95.0, "動画を生成中です")
                video_path = engine.video(manifest.work_id, style=style)
                if video_path:
                    output_path = video_path

            final_path = str(output_path) if output_path else None
            job.output_path = final_path
            job.result = {
                "work_id": manifest.work_id,
                "work_title": manifest.work_title,
                "chapters": len(manifest.chapters),
                "sentences": manifest.total_count,
                "output_path": final_path,
            }
            await self._notify(job, "completed", 100.0, "完了しました")
        except Exception as exc:  # pragma: no cover - エラーハンドリング網羅のため
            logger.exception(f"Batch job {job_id} failed")
            job.error = str(exc)
            await self._notify(job, "failed", job.percent, f"失敗しました: {exc}")

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

    def _event(self, job: BatchJob, status: str, percent: float, message: str) -> dict:
        return {
            "job_id": job.id,
            "status": status,
            "percent": percent,
            "message": message,
            "work_id": job.work_id,
            "output_path": job.output_path,
            "error": job.error,
        }

    def _get_or_raise(self, job_id: str) -> BatchJob:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Job not found: {job_id}")
        return job
