"""CFG-001の型付きSettings、優先順位、パス境界テスト."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from yomiage.config import load_config, load_settings
from yomiage.settings import Settings


def _voicevox_config(**overrides: object) -> dict:
    data: dict = {
        "tts": {"primary_provider": "voicevox"},
        "voicevox": {"url": "http://localhost:50021"},
    }
    data.update(overrides)
    return data


def test_unknown_provider_is_rejected_before_startup() -> None:
    with pytest.raises(ValidationError):
        Settings.from_dict({"tts": {"primary_provider": "not-a-provider"}})

    for section in ("providers", "batch", "storage", "paths", "ollama"):
        with pytest.raises((ValidationError, ValueError)):
            Settings.from_dict({section: "not-a-mapping"})


@pytest.mark.parametrize("section", ["voicevox", "voisona"])
def test_provider_url_type_and_scheme_are_validated(section: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_dict(_voicevox_config(**{section: {"url": 123}}))
    with pytest.raises(ValidationError):
        Settings.from_dict(_voicevox_config(**{section: {"url": "ftp://engine"}}))


@pytest.mark.parametrize("value", [0, -1, 257, "2", 1.5])
def test_synth_concurrency_is_strict_and_bounded(value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.from_dict(_voicevox_config(batch={"synth_concurrency": value}))


def test_required_voisona_secrets_are_checked_only_when_active() -> None:
    inactive = _voicevox_config(voisona={"username": "", "password": ""})
    Settings.from_dict(inactive)

    with pytest.raises(ValueError, match="voisona requires"):
        Settings.from_dict(
            {
                "tts": {"primary_provider": "voisona"},
                "voisona": {"url": "http://voisona:32766"},
            }
        )

    cloud = Settings.from_dict(
        {"llm": {"backend": "openai"}},
        validate_active=False,
    )
    assert cloud.llm.backend == "openai"


def test_cloud_llm_is_default_and_local_llm_is_disabled() -> None:
    settings = Settings.from_dict(_voicevox_config())

    assert settings.llm.backend == "opencode_go"
    assert settings.llm.model == "qwen3.8-flash"
    assert settings.llm.summary_model is None
    assert settings.llm.providers["local"].type == "llama_cpp"
    assert settings.llm.providers["local"].enabled is False
    assert settings.llm.providers["opencode_go"].enabled is True


def test_voicepeak_executable_is_checked_only_when_active(tmp_path: Path) -> None:
    missing = tmp_path / "missing-voicepeak"
    Settings.from_dict(
        {
            "tts": {"primary_provider": "voicevox"},
            "voicepeak": {"path": str(missing)},
        }
    )
    with pytest.raises(ValueError, match="does not exist"):
        Settings.from_dict(
            {
                "tts": {"primary_provider": "voicepeak"},
                "voicepeak": {"path": str(missing)},
            }
        )

    executable = tmp_path / "voicepeak"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    settings = Settings.from_dict(
        {
            "tts": {"primary_provider": "voicepeak"},
            "voicepeak": {"path": str(executable)},
        }
    )
    assert settings.providers.voicepeak.executable == executable.resolve()

    disabled_fallback = Settings.from_dict(
        {
            "tts": {"primary_provider": "voicevox", "fallback_provider": "voicepeak"},
            "voicepeak": {"enabled": False, "path": str(missing)},
        }
    )
    assert disabled_fallback.providers.voicepeak.enabled is False


def test_paths_are_absolute_and_categories_are_distinct(tmp_path: Path) -> None:
    settings = Settings.from_dict(
        _voicevox_config(
            batch={"output_dir": "output"},
            storage={"data_dir": "data", "assets_dir": "assets"},
        ),
        base_dir=tmp_path,
    )
    assert settings.storage.packaged_config_dir.is_absolute()
    assert settings.storage.user_config_dir.is_absolute()
    assert settings.storage.data_dir == (tmp_path / "data").resolve()
    assert settings.storage.output_dir == (tmp_path / "output").resolve()
    assert settings.storage.assets_dir == (tmp_path / "assets").resolve()
    assert settings.storage.sqlite_path == (tmp_path / "data" / "jobs.sqlite3").resolve()
    assert settings.storage.external_binary_dir.is_absolute()
    assert settings.paths.executable == settings.storage.external_binary_dir
    assert settings.paths.output == settings.storage.output_dir

    aliases = Settings.from_dict(
        _voicevox_config(storage={"data": "d", "output": "o", "assets": "a"}),
        base_dir=tmp_path,
    )
    assert aliases.storage.data_dir == (tmp_path / "d").resolve()
    assert aliases.storage.output_dir == (tmp_path / "o").resolve()
    assert aliases.storage.assets_dir == (tmp_path / "a").resolve()

    paths_alias = Settings.from_dict(
        _voicevox_config(paths={"output": "path-output"}), base_dir=tmp_path
    )
    assert paths_alias.paths.output == (tmp_path / "path-output").resolve()


def test_config_precedence_is_packaged_user_environment_then_override(tmp_path: Path) -> None:
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "default.yaml").write_text(
        "tts:\n  primary_provider: voicevox\n  max_chunk_chars: 100\n",
        encoding="utf-8",
    )
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.yaml").write_text("tts:\n  max_chunk_chars: 200\n", encoding="utf-8")

    settings = load_settings(
        packaged,
        user_config_dir=user,
        env={"YOMIAGE_TTS_MAX_CHUNK_CHARS": "300"},
        overrides={"tts": {"max_chunk_chars": 400}},
        base_dir=tmp_path,
    )
    assert settings.tts.max_chunk_chars == 400

    output_override = load_settings(
        packaged,
        user_config_dir=user,
        env={"YOMIAGE_OUTPUT_DIR": "env-output"},
        base_dir=tmp_path,
    )
    assert output_override.storage.output_dir == (tmp_path / "env-output").resolve()
    assert output_override.batch.output_dir == output_override.storage.output_dir

    credential_pkg = tmp_path / "credential"
    credential_pkg.mkdir()
    (credential_pkg / "default.yaml").write_text(
        "tts:\n  primary_provider: voisona\nvoisona:\n"
        "  url: http://voisona\n  username: file-user\n  password: file-pass\n",
        encoding="utf-8",
    )
    credential = load_settings(
        credential_pkg,
        user_config_dir=tmp_path / "missing-user",
        env={"VOISONA_USERNAME": "env-user", "VOISONA_PASSWORD": "env-pass"},
        base_dir=tmp_path,
    )
    assert credential.providers.voisona.username == "env-user"
    assert credential.providers.voisona.password is not None
    assert credential.providers.voisona.password.get_secret_value() == "env-pass"
    explicit = load_settings(
        credential_pkg,
        user_config_dir=tmp_path / "missing-user",
        env={"VOISONA_USERNAME": "env-user", "VOISONA_PASSWORD": "env-pass"},
        overrides={"voisona": {"username": "override", "password": "override"}},
        base_dir=tmp_path,
    )
    assert explicit.providers.voisona.username == "override"


def test_load_config_remains_legacy_adapter(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text(
        "voicevox:\n  url: ${VOICEVOX_URL}\n", encoding="utf-8"
    )
    raw = load_config(config_dir, env={"VOICEVOX_URL": "http://engine:50021"})
    assert raw["voicevox"]["url"] == "http://engine:50021"

    (config_dir / "default.yaml").write_text(
        "voicevox:\n  url: http://file:50021\n", encoding="utf-8"
    )
    raw = load_config(
        config_dir,
        env={"YOMIAGE_VOICEVOX_URL": "http://env:50021"},
    )
    assert raw["voicevox"]["url"] == "http://env:50021"


def test_legacy_adapter_unmasks_secrets_only_at_boundary() -> None:
    settings = Settings.from_dict(
        {
            "tts": {"primary_provider": "voisona"},
            "voisona": {
                "url": "http://voisona:32766",
                "username": "user",
                "password": "secret",
            },
        }
    )
    assert "secret" not in repr(settings)
    assert settings.to_legacy_dict()["voisona"]["password"] == "secret"


def test_llm_provider_secret_is_masked_until_composition_boundary() -> None:
    settings = Settings.from_dict(
        _voicevox_config(
            llm={
                "providers": {
                    "local": {
                        "type": "llama_cpp",
                        "base_url": "http://127.0.0.1:8080/v1",
                        "model": "Qwen3.8-27B",
                        "protocol": "chat_completions",
                    },
                    "opencode_go": {
                        "type": "opencode_go",
                        "base_url": "https://opencode.ai/zen/go/v1",
                        "api_key": "cloud-secret",
                        "model": "qwen3.8-flash",
                        "protocol": "auto",
                    },
                }
            }
        )
    )

    assert "cloud-secret" not in repr(settings)
    assert (
        settings.to_legacy_dict()["llm"]["providers"]["opencode_go"]["api_key"]
        == "cloud-secret"
    )


def test_f18_pipeline_config_preserves_provider_extra_settings_round_trip() -> None:
    from yomiage.api.config import PipelineConfig

    raw = {
        "tts": {"primary_provider": "voicepeak", "fallback_provider": "voicevox"},
        "voicepeak": {
            "path": "/opt/voicepeak",
            "default_narrator": "Japanese Female 1",
            "pitch_scale": 240,
            "max_chars": 120,
        },
        "voicevox": {
            "url": "http://127.0.0.1:50021",
            "default_speaker": 47,
            "speakers": {"gentle": 49},
        },
    }

    config = PipelineConfig.from_dict(raw)

    assert config.tts.to_provider_dict() == raw["voicepeak"]
    assert config.tts_fallback is not None
    assert config.tts_fallback.to_provider_dict() == raw["voicevox"]
