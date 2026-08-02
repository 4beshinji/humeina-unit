"""In-process metrics for synthesis and job execution.

The collector intentionally stores counts and bounded labels only.  It never
stores synthesis text, request bodies, credentials, or complete URLs.  Values
are process metrics; durable job counts are obtained from the JobRepository and
are exposed separately by the HTTP endpoint.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

_LABEL_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")
_ERROR_CATEGORIES = frozenset(
    {
        "unavailable",
        "timeout",
        "rate_limit",
        "authentication",
        "invalid_request",
        "invalid_response",
        "synthesis",
        "cancelled",
        "validation",
        "provider",
        "storage",
        "internal",
        "unknown",
    }
)


def _label(value: object, *, fallback: str = "unknown") -> str:
    """Return a short metric label without accepting arbitrary payload data."""

    normalized = _LABEL_RE.sub("_", str(value or "")).strip("_")[:80]
    return normalized or fallback


def _category(value: object) -> str:
    normalized = _label(value)
    return normalized if normalized in _ERROR_CATEGORIES else "unknown"


@dataclass
class MetricsCollector:
    """Thread-safe process metrics shared by all synthesis entrypoints."""

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
    fallback_total: int = 0
    partial_artifacts_total: int = 0
    queue_depth: int = 0
    queue_depth_max: int = 0
    active_jobs: int = 0
    active_jobs_max: int = 0
    _job_states: Counter[str] = field(default_factory=Counter)
    _current_job_states: dict[str, str] = field(default_factory=dict)
    _error_categories: Counter[str] = field(default_factory=Counter)
    _provider_synthesis: dict[str, Counter[str]] = field(default_factory=dict)
    _provider_latency_ms: defaultdict[str, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    _lock: Lock = field(default_factory=Lock)

    def record_synthesis(
        self,
        duration_ms: float,
        error: bool = False,
        cache_hit: bool = False,
        text: str = "",
        *,
        text_length: int | None = None,
        provider: str | None = None,
        error_category: str | None = None,
        fallback_used: bool = False,
        partial: bool = False,
    ) -> None:
        """Record one logical synthesis request.

        ``text`` is accepted for compatibility but only its length is retained.
        New callers should pass ``text_length`` so a request body never reaches
        this object at all.
        """

        try:
            duration = max(0.0, float(duration_ms))
        except (TypeError, ValueError):
            duration = 0.0
        length = text_length if text_length is not None else len(text)
        try:
            length = max(0, int(length))
        except (TypeError, ValueError):
            length = 0
        provider_label = _label(provider) if provider else "unknown"
        with self._lock:
            self.synthesis_total += 1
            self.synthesis_duration_ms_total += duration
            self.synthesis_duration_ms_max = max(self.synthesis_duration_ms_max, duration)
            self.synthesis_chars_total += length
            provider_counts = self._provider_synthesis.setdefault(
                provider_label,
                Counter(total=0, errors=0, cache_hits=0, cache_misses=0),
            )
            provider_counts["total"] += 1
            self._provider_latency_ms[provider_label] += duration
            if error:
                self.synthesis_errors += 1
                provider_counts["errors"] += 1
                self._error_categories[_category(error_category)] += 1
            if cache_hit:
                self.synthesis_cache_hits += 1
                provider_counts["cache_hits"] += 1
            else:
                self.synthesis_cache_misses += 1
                provider_counts["cache_misses"] += 1
            if fallback_used:
                self.fallback_total += 1
            if partial:
                self.partial_artifacts_total += 1

    def record_batch_job(self, status: str, *, job_id: str | None = None) -> None:
        """Record a batch terminal state (legacy API retained)."""

        normalized = _label(status)
        with self._lock:
            self.batch_jobs_total += 1
            self._job_states[normalized] += 1
            if job_id is not None:
                self._current_job_states[_label(job_id)] = normalized
            if normalized in {"completed", "complete_with_warnings"}:
                self.batch_jobs_completed += 1
            elif normalized in {"failed", "partial"}:
                self.batch_jobs_failed += 1
            if normalized == "partial":
                self.partial_artifacts_total += 1

    def record_job_state(self, status: str, *, job_id: str | None = None) -> None:
        """Record a transition and, when identified, the job's current state."""

        with self._lock:
            normalized = _label(status)
            self._job_states[normalized] += 1
            if job_id is not None:
                self._current_job_states[_label(job_id)] = normalized

    def record_error(self, category: str) -> None:
        """Record a non-synthesis error category without retaining its message."""

        with self._lock:
            self._error_categories[_category(category)] += 1

    def record_fallback(self, count: int = 1) -> None:
        with self._lock:
            self.fallback_total += max(0, int(count))

    def record_partial(self, count: int = 1) -> None:
        with self._lock:
            self.partial_artifacts_total += max(0, int(count))

    def set_queue_depth(self, depth: int, *, active_jobs: int | None = None) -> None:
        """Update current queue/active gauges and their process maxima."""

        with self._lock:
            self.queue_depth = max(0, int(depth))
            self.queue_depth_max = max(self.queue_depth_max, self.queue_depth)
            if active_jobs is not None:
                self.active_jobs = max(0, int(active_jobs))
                self.active_jobs_max = max(self.active_jobs_max, self.active_jobs)

    # Common aliases used by adapters and tests.
    update_queue = set_queue_depth

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
        """Return a stable, secret-free JSON-compatible snapshot."""

        with self._lock:
            provider_counts = {
                provider: {
                    **dict(counts),
                    "average_duration_ms": (
                        self._provider_latency_ms.get(provider, 0.0) / counts["total"]
                        if counts["total"]
                        else 0.0
                    ),
                }
                for provider, counts in self._provider_synthesis.items()
            }
            job_transitions = dict(self._job_states)
            job_states = dict(Counter(self._current_job_states.values()))
            error_categories = dict(self._error_categories)
            synthesis = {
                "total": self.synthesis_total,
                "errors": self.synthesis_errors,
                "error_rate": self.synthesis_error_rate,
                "error_categories": error_categories,
                "cache_hits": self.synthesis_cache_hits,
                "cache_misses": self.synthesis_cache_misses,
                "cache_hit_rate": self.synthesis_cache_hit_rate,
                "average_duration_ms": self.synthesis_average_duration_ms,
                "max_duration_ms": self.synthesis_duration_ms_max,
                "total_chars": self.synthesis_chars_total,
                "fallbacks": self.fallback_total,
                "partial_artifacts": self.partial_artifacts_total,
                "by_provider": provider_counts,
            }
            jobs = {
                "states": job_states,
                "by_state": job_states,
                "transitions": job_transitions,
                "queue_depth": self.queue_depth,
                "queue_depth_max": self.queue_depth_max,
                "active_jobs": self.active_jobs,
                "active_jobs_max": self.active_jobs_max,
            }
            batch_jobs = {
                "total": self.batch_jobs_total,
                "completed": self.batch_jobs_completed,
                "failed": self.batch_jobs_failed,
            }
            return {
                "synthesis": synthesis,
                "batch_jobs": batch_jobs,
                "jobs": jobs,
                # Top-level aliases make dashboards independent of the older
                # nested schema while preserving the existing endpoint shape.
                "job_states": job_states,
                "job_state_transitions": job_transitions,
                "queue_depth": self.queue_depth,
                "active_jobs": self.active_jobs,
                "fallbacks": self.fallback_total,
                "partial_artifacts": self.partial_artifacts_total,
                "error_categories": error_categories,
                "process": {
                    "synthesis_total": self.synthesis_total,
                    "batch_jobs_total": self.batch_jobs_total,
                },
            }


__all__ = ["MetricsCollector"]
