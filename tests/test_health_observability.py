"""Focused OBS-001 health and synthesis-metrics contracts."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from yomiage.api.metrics import MetricsCollector
from yomiage.application.health import HealthCheck, HealthDependencies
from yomiage.application.synthesis import SynthesisService
from yomiage.ports.tts import (
    ProviderHealth,
    ProviderSynthesisRequest,
    ProviderSynthesisResult,
)
from yomiage.server import ServerDependencies, create_app


class _Provider:
    name = "fake"

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.healthy, provider=self.name)

    async def list_voices(self):
        return []

    async def synthesize(self, request: ProviderSynthesisRequest) -> ProviderSynthesisResult:
        return ProviderSynthesisResult(audio_data=b"RIFF")

    async def close(self) -> None:
        return None


class _LLM:
    async def is_available(self) -> bool:
        return True


def _health_app(tmp_path: Path, *, provider: _Provider):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "output"
    data_dir.mkdir()
    output_dir.mkdir()
    dependencies = HealthDependencies(
        provider=provider,
        llm=_LLM(),
        ffmpeg=sys.executable,
        storage={"data_dir": data_dir, "output_dir": output_dir},
    )
    return create_app(
        {"storage": {"sqlite_path": str(data_dir / "jobs.sqlite3")}},
        ServerDependencies(
            engine=object(),
            health_dependencies=dependencies,
        ),
    )


def test_liveness_stays_up_when_tts_is_unavailable(tmp_path: Path) -> None:
    with TestClient(_health_app(tmp_path, provider=_Provider(False))) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json()["live"] is True
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert ready.json()["checks"]["provider"]["healthy"] is False
    assert str(tmp_path) not in ready.text


def test_synthesis_service_records_metrics_without_text_payload() -> None:
    metrics = MetricsCollector()
    service = SynthesisService(
        {"fake": _Provider()},
        primary="fake",
        metrics=metrics,
    )

    import asyncio

    asyncio.run(service.synthesize("秘密の本文"))
    payload = metrics.to_dict()

    assert payload["synthesis"]["total"] == 1
    assert payload["synthesis"]["total_chars"] == 5
    assert "秘密の本文" not in str(payload)
    assert payload["synthesis"]["by_provider"]["fake"]["total"] == 1


def test_job_metrics_separate_current_state_from_transition_counts() -> None:
    metrics = MetricsCollector()

    metrics.record_job_state("analyzing", job_id="job-1")
    metrics.record_job_state("analyzing", job_id="job-1")
    metrics.record_job_state("synthesizing", job_id="job-1")
    metrics.record_job_state("analyzing", job_id="job-2")
    metrics.record_batch_job("completed", job_id="job-1")

    payload = metrics.to_dict()
    assert payload["jobs"]["by_state"] == {"completed": 1, "analyzing": 1}
    assert payload["jobs"]["transitions"] == {
        "analyzing": 3,
        "synthesizing": 1,
        "completed": 1,
    }


def test_health_details_drop_arbitrary_secret_text() -> None:
    payload = HealthCheck(
        False,
        "token=secret",
        {
            "token": "secret-value",
            "url": "https://user:password@example.invalid",
            "providers": {"primary": False},
        },
    ).to_dict()

    assert payload["status"] == "unknown"
    assert payload["details"] == {"providers": {"primary": False}}
    assert "secret" not in str(payload)
