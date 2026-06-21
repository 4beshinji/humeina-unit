"""Tests for batch synthesis REST API endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from yomiage.batch.job_manager import BatchJobManager
from yomiage.server import app


@pytest.fixture
def client(monkeypatch):
    """TestClient with mocked engine and job manager."""
    mock_engine = MagicMock()
    mock_engine.is_running = False
    mock_engine.current_status = {}
    mock_engine.tts = MagicMock()

    manager = BatchJobManager({})
    monkeypatch.setattr(manager, "run_job", AsyncMock())

    monkeypatch.setattr("yomiage.server._engine", mock_engine)
    monkeypatch.setattr("yomiage.server._job_manager", manager)

    return TestClient(app)


class TestBatchSynthesizeEndpoints:
    def test_create_batch_job(self, client):
        response = client.post(
            "/api/yomiage/synthesize/batch",
            json={"url": "https://example.com/novel"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"

    def test_create_batch_job_with_options(self, client):
        response = client.post(
            "/api/yomiage/synthesize/batch",
            json={
                "url": "https://example.com/novel",
                "mode": "voicevox",
                "output_format": "mp3",
                "video": True,
                "style": "portrait",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data

    def test_get_batch_job(self, client):
        create_resp = client.post(
            "/api/yomiage/synthesize/batch",
            json={"url": "https://example.com/novel"},
        )
        job_id = create_resp.json()["job_id"]

        response = client.get(f"/api/yomiage/synthesize/batch/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["url"] == "https://example.com/novel"
        assert data["status"] == "pending"

    def test_get_batch_job_not_found(self, client):
        response = client.get("/api/yomiage/synthesize/batch/notfound")
        assert response.status_code == 404

    def test_batch_job_progress_sse(self, client, monkeypatch):
        create_resp = client.post(
            "/api/yomiage/synthesize/batch",
            json={"url": "https://example.com/novel"},
        )
        job_id = create_resp.json()["job_id"]

        from yomiage import server as server_mod

        manager = server_mod._job_manager
        test_queue: asyncio.Queue[dict] = asyncio.Queue()

        def mock_subscribe(jid: str) -> asyncio.Queue[dict]:
            job = manager.get_job(jid)
            test_queue.put_nowait(
                manager._event(job, job.status, job.percent, job.message)
            )
            return test_queue

        monkeypatch.setattr(manager, "subscribe", mock_subscribe)
        test_queue.put_nowait(
            {
                "job_id": job_id,
                "status": "completed",
                "percent": 100.0,
                "message": "完了しました",
                "work_id": None,
                "output_path": None,
                "error": None,
            }
        )

        with client.stream(
            "GET",
            f"/api/yomiage/synthesize/batch/{job_id}/progress",
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            text = response.read().decode()
            assert "event: progress" in text
            assert '"status": "completed"' in text
            assert "完了しました" in text


class TestBatchJobManager:
    @pytest.mark.asyncio
    async def test_create_and_get_job(self):
        manager = BatchJobManager({})
        job = manager.create_job("https://example.com/novel", mode="voicevox")
        assert job.status == "pending"
        assert job.mode == "voicevox"
        assert manager.get_job(job.id) is job

    @pytest.mark.asyncio
    async def test_subscribe_receives_current_state(self):
        manager = BatchJobManager({})
        job = manager.create_job("https://example.com/novel")
        queue = manager.subscribe(job.id)
        first = queue.get_nowait()
        assert first["job_id"] == job.id
        assert first["status"] == "pending"

    @pytest.mark.asyncio
    async def test_notify_updates_job_and_queues(self):
        manager = BatchJobManager({})
        job = manager.create_job("https://example.com/novel")
        queue = manager.subscribe(job.id)
        queue.get_nowait()  # 初期イベントを捨てる

        await manager._notify(job, "analyzing", 50.0, "分析中")
        assert job.status == "analyzing"
        assert job.percent == 50.0
        assert job.message == "分析中"

        event = queue.get_nowait()
        assert event["status"] == "analyzing"
        assert event["percent"] == 50.0

    @pytest.mark.asyncio
    async def test_run_job_success(self, monkeypatch):
        manager = BatchJobManager({})
        job = manager.create_job("https://example.com/novel", mode="voicevox")

        # BatchEngine をモック化
        mock_engine = MagicMock()
        mock_manifest = MagicMock()
        mock_manifest.work_id = "work123"
        mock_manifest.work_title = "Test Novel"
        mock_manifest.chapters = [1, 2]
        mock_manifest.total_count = 10

        mock_engine.analyze = MagicMock(return_value=asyncio.Future())
        mock_engine.analyze.return_value.set_result(mock_manifest)
        mock_engine.synthesize = MagicMock(return_value=asyncio.Future())
        mock_engine.synthesize.return_value.set_result(mock_manifest)
        mock_engine.concat = MagicMock(return_value="/tmp/output.wav")

        mock_engine_cls = MagicMock(return_value=mock_engine)
        monkeypatch.setattr("yomiage.batch.job_manager.BatchEngine", mock_engine_cls)

        events = []
        queue = manager.subscribe(job.id)
        queue.get_nowait()  # 初期イベント

        await manager.run_job(job.id)

        while not queue.empty():
            events.append(queue.get_nowait())

        assert job.status == "completed"
        assert job.percent == 100.0
        assert job.output_path == "/tmp/output.wav"
        assert any(e["status"] == "analyzing" for e in events)
        assert any(e["status"] == "synthesizing" for e in events)
        assert any(e["status"] == "completed" for e in events)
