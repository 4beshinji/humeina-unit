"""Tests for SDK metrics collector."""

from __future__ import annotations

import pytest

from yomiage.api.metrics import MetricsCollector


class TestMetricsCollector:
    def test_record_synthesis(self):
        metrics = MetricsCollector()
        metrics.record_synthesis(duration_ms=120.0, text="hello")

        assert metrics.synthesis_total == 1
        assert metrics.synthesis_average_duration_ms == pytest.approx(120.0)
        assert metrics.synthesis_cache_hit_rate == 0.0

    def test_record_cache_hit(self):
        metrics = MetricsCollector()
        metrics.record_synthesis(duration_ms=10.0, cache_hit=True, text="cached")
        metrics.record_synthesis(duration_ms=100.0, cache_hit=False, text="new")

        assert metrics.synthesis_cache_hits == 1
        assert metrics.synthesis_cache_misses == 1
        assert metrics.synthesis_cache_hit_rate == 0.5

    def test_record_error(self):
        metrics = MetricsCollector()
        metrics.record_synthesis(duration_ms=50.0, error=True, text="fail")

        assert metrics.synthesis_errors == 1
        assert metrics.synthesis_error_rate == 1.0

    def test_record_batch_job(self):
        metrics = MetricsCollector()
        metrics.record_batch_job("completed")
        metrics.record_batch_job("completed")
        metrics.record_batch_job("failed")

        assert metrics.batch_jobs_total == 3
        assert metrics.batch_jobs_completed == 2
        assert metrics.batch_jobs_failed == 1

    def test_to_dict(self):
        metrics = MetricsCollector()
        metrics.record_synthesis(duration_ms=100.0, text="hello")
        metrics.record_batch_job("completed")

        data = metrics.to_dict()
        assert data["synthesis"]["total"] == 1
        assert data["synthesis"]["average_duration_ms"] == 100.0
        assert data["batch_jobs"]["completed"] == 1
