"""Tests for server metrics endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from yomiage.api.metrics import MetricsCollector
from yomiage.server import app


@pytest.fixture
def client(monkeypatch):
    mock_engine = MagicMock()
    mock_engine.is_running = False
    mock_engine.current_status = {}
    mock_engine.tts = MagicMock()

    metrics = MetricsCollector()
    metrics.record_synthesis(duration_ms=120.0, text="hello")
    metrics.record_batch_job("completed")

    monkeypatch.setattr("yomiage.server._engine", mock_engine)
    monkeypatch.setattr("yomiage.server._metrics", metrics)

    return TestClient(app)


class TestMetricsEndpoint:
    def test_get_metrics(self, client):
        response = client.get("/api/yomiage/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["synthesis"]["total"] == 1
        assert data["synthesis"]["average_duration_ms"] == 120.0
        assert data["batch_jobs"]["completed"] == 1

    def test_get_metrics_not_initialized(self, monkeypatch):
        monkeypatch.setattr("yomiage.server._engine", MagicMock())
        monkeypatch.setattr("yomiage.server._metrics", None)

        client = TestClient(app)
        response = client.get("/api/yomiage/metrics")
        assert response.status_code == 503
