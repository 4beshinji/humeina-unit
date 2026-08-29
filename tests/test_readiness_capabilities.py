"""F20/D04 operation-specific readiness contracts。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from yomiage.application.health import HealthChecker, HealthDependencies
from yomiage.config import load_settings
from yomiage.ports.tts import (
    ProviderCapabilities,
    ProviderHealth,
    ProviderOutputMode,
)
from yomiage.server import ServerDependencies, create_app
from yomiage.settings import Settings


class _Provider:
    def __init__(
        self,
        *modes: ProviderOutputMode,
        formats: frozenset[str] = frozenset({"wav"}),
        healthy: bool = True,
    ) -> None:
        self.name = "fake"
        self.capabilities = ProviderCapabilities(
            output_modes=frozenset(modes),
            audio_formats=formats,
        )
        self._healthy = healthy

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self._healthy, provider=self.name)


async def _available() -> bool:
    return True


def test_f20_packaged_batch_output_is_used_as_storage_dependency(tmp_path: Path) -> None:
    output = tmp_path / "custom-output"
    output.mkdir()
    checker = HealthChecker(config={"batch": {"output_dir": str(output)}})

    entries = checker._storage_entries()

    assert entries == [("output", output)]

    check = asyncio.run(checker._storage_check())
    assert check.healthy is True
    assert check.details["paths"] == {"output": True}
    assert check.details["capacity_bytes"]["output"] > 0


def test_f20_packaged_settings_report_the_injected_runtime_output(tmp_path: Path) -> None:
    settings = load_settings(base_dir=tmp_path, env={}, validate_active=False)
    settings.storage.data_dir.mkdir(parents=True, exist_ok=True)
    settings.batch.output_dir.mkdir(parents=True, exist_ok=True)
    checker = HealthChecker(config=settings.to_legacy_dict())

    entries = dict(checker._storage_entries())
    check = asyncio.run(checker._storage_check())

    assert entries["output"] == settings.batch.output_dir
    assert check.healthy is True
    assert check.details["paths"]["output"] is True
    assert check.details["capacity_bytes"]["output"] > 0


@pytest.mark.parametrize(
    ("mode", "immediate_ready", "batch_ready"),
    [
        (ProviderOutputMode.AUDIO_BYTES, True, False),
        (ProviderOutputMode.LOCAL_FILE, False, True),
        (ProviderOutputMode.VM_FILE, False, True),
    ],
)
def test_d04_provider_target_capability_matrix(
    tmp_path: Path,
    mode: ProviderOutputMode,
    immediate_ready: bool,
    batch_ready: bool,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    checker = HealthChecker(
        config={"batch": {"output_dir": str(output)}},
        dependencies=HealthDependencies(
            provider=_Provider(mode),
            llm=_available,
            ffmpeg=True,
            vm_mount=True,
        ),
    )

    operations = asyncio.run(checker.operation_readiness())

    assert operations["immediate_playback"]["ready"] is immediate_ready
    assert operations["batch_audio"]["ready"] is batch_ready
    assert ("provider" in operations["batch_audio"]["missing"]) is not batch_ready
    assert operations["video"]["ready"] is batch_ready
    assert operations["slack"]["ready"] is False
    assert "slack" in operations["slack"]["missing"]


def test_d04_format_and_health_are_part_of_provider_readiness(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    checker = HealthChecker(
        config={"batch": {"output_dir": str(output)}},
        providers={
            "wrong-format": _Provider(
                ProviderOutputMode.AUDIO_BYTES,
                formats=frozenset({"mp3"}),
            ),
            "unhealthy": _Provider(ProviderOutputMode.LOCAL_FILE, healthy=False),
        },
        ffmpeg=True,
    )

    operations = asyncio.run(checker.operation_readiness())

    assert operations["immediate_playback"] == {
        "ready": False,
        "missing": ["provider"],
    }
    assert operations["batch_audio"]["ready"] is False
    assert "provider" in operations["batch_audio"]["missing"]


def test_d04_vm_file_and_slack_require_mount_and_sdk(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    unavailable = HealthChecker(
        config={"batch": {"output_dir": str(output)}},
        dependencies=HealthDependencies(
            provider=_Provider(
                ProviderOutputMode.AUDIO_BYTES,
                ProviderOutputMode.VM_FILE,
            ),
            llm=True,
            ffmpeg=True,
            slack=True,
            slack_sdk=False,
            vm_mount=False,
        ),
    )

    missing = asyncio.run(unavailable.operation_readiness())

    assert missing["batch_audio"] == {
        "ready": False,
        "missing": ["vm_mount"],
    }
    assert missing["slack"] == {
        "ready": False,
        "missing": ["slack_sdk"],
    }

    available = HealthChecker(
        config={"batch": {"output_dir": str(output)}},
        dependencies=HealthDependencies(
            provider=_Provider(
                ProviderOutputMode.AUDIO_BYTES,
                ProviderOutputMode.VM_FILE,
            ),
            llm=True,
            ffmpeg=True,
            slack=True,
            slack_sdk=True,
            vm_mount=True,
        ),
    )

    ready = asyncio.run(available.operation_readiness())

    assert ready["batch_audio"] == {"ready": True, "missing": []}
    assert ready["slack"] == {"ready": True, "missing": []}


def test_d04_readiness_reports_each_operation_and_missing_capabilities(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    data = tmp_path / "data"
    output.mkdir()
    data.mkdir()
    settings = Settings.from_dict(
        {
            "tts": {"primary_provider": "voicevox"},
            "voicevox": {"url": "http://127.0.0.1:50021"},
            "storage": {
                "output_dir": output,
                "data_dir": data,
                "sqlite_path": data / "jobs.sqlite3",
            },
        },
        base_dir=tmp_path,
        validate_active=False,
    )
    checker = HealthChecker(config=settings.to_legacy_dict())

    operations = asyncio.run(checker.operation_readiness())

    assert set(operations) >= {"immediate_playback", "batch_audio", "video", "slack"}
    assert operations["batch_audio"]["ready"] is False
    assert "provider" in operations["batch_audio"]["missing"]
    assert "ffmpeg" in operations["video"]["missing"]


def test_d04_http_readiness_returns_operation_capability_matrix(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    checker = HealthChecker(
        config={"batch": {"output_dir": str(output)}},
        dependencies=HealthDependencies(
            provider=_Provider(
                ProviderOutputMode.AUDIO_BYTES,
                ProviderOutputMode.VM_FILE,
            ),
            llm=SimpleNamespace(is_available=_available),
            ffmpeg=True,
            storage={"output_dir": output},
            sqlite=True,
            slack=True,
            slack_sdk=False,
            vm_mount=False,
        ),
    )
    application = create_app(
        {"storage": {"sqlite_path": str(tmp_path / "jobs.sqlite3")}},
        ServerDependencies(
            engine=SimpleNamespace(is_running=False, current_status={}),
            health=checker,
        ),
    )

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["operations"]) == {
        "immediate_playback",
        "batch_audio",
        "video",
        "slack",
    }
    assert payload["operations"]["immediate_playback"] == {
        "ready": True,
        "missing": [],
    }
    assert payload["operations"]["batch_audio"] == {
        "ready": False,
        "missing": ["vm_mount"],
    }
    assert payload["operations"]["slack"] == {
        "ready": False,
        "missing": ["slack_sdk"],
    }
    assert str(tmp_path) not in response.text
