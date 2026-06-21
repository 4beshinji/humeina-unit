"""Metrics collector for the TTS SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class MetricsCollector:
    """TTS 合成・バッチジョブの実行メトリクスを収集する.

    スレッドセーフではないが、asyncio 単一スレッド内で使用することを想定。
    外部からロックが必要な場合は呼び出し側で保護してください。
    """

    synthesis_total: int = 0
    synthesis_errors: int = 0
    synthesis_cache_hits: int = 0
    synthesis_cache_misses: int = 0
    synthesis_duration_ms_total: float = 0.0
    synthesis_duration_ms_max: float = 0.0
    synthesis_chars_total: int = 0
    batch_jobs_total: int = 0
    batch_jobs_completed: int = 0
    batch_jobs_failed: int = 0
    _lock: Lock = field(default_factory=Lock)

    def record_synthesis(
        self,
        duration_ms: float,
        error: bool = False,
        cache_hit: bool = False,
        text: str = "",
    ) -> None:
        """1回の合成を記録する."""
        with self._lock:
            self.synthesis_total += 1
            self.synthesis_duration_ms_total += duration_ms
            self.synthesis_duration_ms_max = max(
                self.synthesis_duration_ms_max, duration_ms
            )
            self.synthesis_chars_total += len(text)
            if error:
                self.synthesis_errors += 1
            if cache_hit:
                self.synthesis_cache_hits += 1
            else:
                self.synthesis_cache_misses += 1

    def record_batch_job(self, status: str) -> None:
        """バッチジョブの状態変化を記録する."""
        with self._lock:
            self.batch_jobs_total += 1
            if status == "completed":
                self.batch_jobs_completed += 1
            elif status == "failed":
                self.batch_jobs_failed += 1

    @property
    def synthesis_average_duration_ms(self) -> float:
        if self.synthesis_total == 0:
            return 0.0
        return self.synthesis_duration_ms_total / self.synthesis_total

    @property
    def synthesis_error_rate(self) -> float:
        if self.synthesis_total == 0:
            return 0.0
        return self.synthesis_errors / self.synthesis_total

    @property
    def synthesis_cache_hit_rate(self) -> float:
        total = self.synthesis_cache_hits + self.synthesis_cache_misses
        if total == 0:
            return 0.0
        return self.synthesis_cache_hits / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "synthesis": {
                "total": self.synthesis_total,
                "errors": self.synthesis_errors,
                "error_rate": self.synthesis_error_rate,
                "cache_hits": self.synthesis_cache_hits,
                "cache_misses": self.synthesis_cache_misses,
                "cache_hit_rate": self.synthesis_cache_hit_rate,
                "average_duration_ms": self.synthesis_average_duration_ms,
                "max_duration_ms": self.synthesis_duration_ms_max,
                "total_chars": self.synthesis_chars_total,
            },
            "batch_jobs": {
                "total": self.batch_jobs_total,
                "completed": self.batch_jobs_completed,
                "failed": self.batch_jobs_failed,
            },
        }
