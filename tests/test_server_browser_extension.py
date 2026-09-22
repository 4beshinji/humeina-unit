"""Firefox拡張向けReader APIの契約テスト。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from yomiage.core.jobs import JobState
from yomiage.server import ServerDependencies, create_app


class _BrowserEngine:
    def __init__(self) -> None:
        self.is_running = False
        self.is_paused = False
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.tts = SimpleNamespace(
            available_provider_names=("voisona", "voicevox"),
            primary=SimpleNamespace(name="voisona"),
        )

    @property
    def current_status(self) -> dict[str, object]:
        return {
            "running": self.is_running,
            "paused": self.is_paused,
            "provider": None,
        }

    async def read_text(self, text: str, **kwargs: object) -> None:
        self.is_running = True
        self.calls.append((text, kwargs))
        self.is_running = False

    async def close(self) -> None:
        return None


def _app(tmp_path, *, max_text_chars: int = 100_000):
    engine = _BrowserEngine()
    application = create_app(
        {
            "server": {
                "max_text_chars": max_text_chars,
                "client_rate_limit": 1_000,
            },
            "storage": {"sqlite_path": str(tmp_path / "jobs.sqlite3")},
        },
        ServerDependencies(engine=engine),
    )
    return application, engine


def _wait_for_job(application, job_id: str):
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        job = application.state.job_repository.get(job_id)
        if job is not None and job.state in {JobState.COMPLETED, JobState.FAILED}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job_id}")


def test_capabilities_describe_browser_reader_contract(tmp_path) -> None:
    application, _engine = _app(tmp_path)

    with TestClient(application) as client:
        response = client.get("/api/yomiage/capabilities")

    assert response.status_code == 200
    assert response.json()["providers"] == ["voisona", "voicevox"]
    assert response.json()["features"]["read_text"] is True


def test_read_text_routes_extracted_selection_to_requested_provider(tmp_path) -> None:
    application, engine = _app(tmp_path)

    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/read-text",
            json={
                "text": "選択した本文です。",
                "source_url": "https://example.com/article",
                "title": "記事",
                "source_kind": "selection",
                "provider": "voicevox",
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        job = _wait_for_job(application, job_id)
        status = client.get(f"/api/yomiage/jobs/{job_id}")

    assert job.state is JobState.COMPLETED
    assert engine.calls == [
        (
            "選択した本文です。",
            {
                "source_url": "https://example.com/article",
                "title": "記事",
                "work_title": "記事",
                "provider": "voicevox",
            },
        )
    ]
    assert status.status_code == 200
    assert status.json()["state"] == "completed"


def test_read_text_rejects_oversized_content_before_job_creation(tmp_path) -> None:
    application, engine = _app(tmp_path, max_text_chars=5)

    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/read-text",
            json={"text": "123456", "provider": "auto"},
        )

    assert response.status_code == 413
    assert engine.calls == []


def test_read_text_rejects_provider_not_loaded_by_runtime(tmp_path) -> None:
    application, engine = _app(tmp_path)
    engine.tts.available_provider_names = ("voisona",)

    with TestClient(application) as client:
        response = client.post(
            "/api/yomiage/read-text",
            json={"text": "本文", "provider": "voicevox"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider_not_configured"
    assert engine.calls == []
