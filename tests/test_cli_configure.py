"""対話セットアップのCLI contract test。"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from yomiage.cli import app


def test_configure_cloud_voicevox_writes_references_but_no_secret(
    tmp_path: Path, monkeypatch
) -> None:
    from yomiage.interfaces.cli import configure as configure_module

    async def fake_voices(_: str) -> list[dict]:
        return [
            {
                "id": 2,
                "name": "四国めたん（ノーマル）",
                "speaker_name": "四国めたん",
                "style_name": "ノーマル",
            },
            {
                "id": 0,
                "name": "四国めたん（あまあま）",
                "speaker_name": "四国めたん",
                "style_name": "あまあま",
            },
            {
                "id": 47,
                "name": "ナースロボ＿タイプT（ノーマル）",
                "speaker_name": "ナースロボ＿タイプT",
                "style_name": "ノーマル",
            },
        ]

    monkeypatch.setattr(
        configure_module,
        "_opencode_secret_reference",
        lambda: {"source": "kwallet", "wallet": "kdewallet"},
    )
    monkeypatch.setattr(configure_module, "_voicevox_voices", fake_voices)
    destination = tmp_path / "config.yaml"
    result = CliRunner().invoke(
        app,
        ["configure", "--user-config", str(destination), "--no-preview"],
        input="\n" * 9,
    )

    assert result.exit_code == 0, result.stdout
    config = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert config["tts"] == {"primary_provider": "voicevox", "fallback_provider": None}
    assert config["voicevox"]["default_speaker"] == 47
    assert config["voicevox"]["speed_scale"] == 1.0
    assert config["llm"]["routes"]["default"]["provider"] == "opencode_go"
    assert config["llm"]["routes"]["default"]["max_output_tokens"] == 16384
    assert config["llm"]["providers"]["local"]["enabled"] is False
    assert config["secrets"]["opencode_go"]["source"] == "kwallet"
    assert "api_key" not in destination.read_text(encoding="utf-8").lower()
    assert destination.stat().st_mode & 0o777 == 0o600


def test_configure_voicevox_selects_character_before_style(
    tmp_path: Path, monkeypatch
) -> None:
    from yomiage.interfaces.cli import configure as configure_module

    async def fake_voices(_: str) -> list[dict]:
        return [
            {
                "id": 2,
                "name": "四国めたん（ノーマル）",
                "speaker_name": "四国めたん",
                "style_name": "ノーマル",
            },
            {
                "id": 0,
                "name": "四国めたん（あまあま）",
                "speaker_name": "四国めたん",
                "style_name": "あまあま",
            },
            {
                "id": 47,
                "name": "ナースロボ＿タイプT（ノーマル）",
                "speaker_name": "ナースロボ＿タイプT",
                "style_name": "ノーマル",
            },
        ]

    monkeypatch.setattr(
        configure_module,
        "_opencode_secret_reference",
        lambda: {"source": "environment", "variable": "OPENCODE_GO_API_KEY"},
    )
    monkeypatch.setattr(configure_module, "_voicevox_voices", fake_voices)
    destination = tmp_path / "config.yaml"
    result = CliRunner().invoke(
        app,
        ["configure", "--user-config", str(destination), "--no-preview"],
        # cloud、VOICEVOX、四国めたん、あまあま、残りは音声パラメーターの既定値。
        input="1\n1\n\n1\n2\n\n\n\n\n",
    )

    assert result.exit_code == 0, result.stdout
    assert "VOICEVOXのキャラクター" in result.stdout
    assert "四国めたんのスタイル" in result.stdout
    config = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert config["voicevox"]["default_speaker"] == 0
