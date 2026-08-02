"""Tests for server metrics endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from yomiage.api.metrics import MetricsCollector
from yomiage.server import ServerDependencies, create_app


@pytest.fixture
def client(tmp_path):
    mock_engine = MagicMock()
    mock_engine.is_running = False
    mock_engine.current_status = {}
    mock_engine.tts = MagicMock()

    metrics = MetricsCollector()
    metrics.record_synthesis(duration_ms=120.0, text="hello")
    metrics.record_batch_job("completed")

    application = create_app(
        {"storage": {"sqlite_path": str(tmp_path / "jobs.sqlite3")}},
        ServerDependencies(engine=mock_engine, metrics=metrics),
    )

    with TestClient(application) as test_client:
        yield test_client


class TestMetricsEndpoint:
    def test_get_metrics(self, client):
        response = client.get("/api/yomiage/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["synthesis"]["total"] == 1
        assert data["synthesis"]["average_duration_ms"] == 120.0
        assert data["batch_jobs"]["completed"] == 1

    def test_get_metrics_is_initialized_per_app(self, tmp_path):
        application = create_app(
            {"storage": {"sqlite_path": str(tmp_path / "jobs.sqlite3")}},
            ServerDependencies(engine=MagicMock()),
        )
        with TestClient(application) as client:
            response = client.get("/api/yomiage/metrics")
        assert response.status_code == 200
        assert response.json()["synthesis"]["total"] == 0
