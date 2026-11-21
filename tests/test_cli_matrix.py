"""CLI command/help matrix contract.

ヘルプ表示は外部 TTS/LLM を起動せずに全 command の登録と主要な command 名を検査する。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from yomiage.cli import app

COMMAND_HELP_CASES = (
    (),
    ("configure",),
    ("read",),
    ("resume",),
    ("voices",),
    ("voices", "list"),
    ("characters",),
    ("characters", "list"),
    ("characters", "assign"),
    ("news",),
    ("slack",),
    ("batch",),
    ("batch", "analyze"),
    ("batch", "synthesize"),
    ("batch", "concat"),
    ("batch", "run"),
    ("batch", "subtitle"),
    ("batch", "video"),
    ("batch", "status"),
    ("batch", "retry"),
    ("tune",),
    ("tune", "range"),
    ("tune", "preset"),
    ("tune", "emotion"),
    ("tune", "noise"),
    ("tune", "demo"),
    ("tune", "test"),
    ("vp-tune",),
    ("vp-tune", "range"),
    ("vp-tune", "preset"),
    ("vp-tune", "emotion"),
    ("vp-tune", "noise"),
    ("vp-tune", "demo"),
    ("vp-tune", "test"),
    ("studio",),
    ("studio", "synth"),
    ("studio", "preview"),
    ("studio", "voices"),
    ("serve",),
)


@pytest.mark.parametrize("command", COMMAND_HELP_CASES)
def test_all_cli_commands_render_help(command: tuple[str, ...]) -> None:
    result = CliRunner().invoke(app, [*command, "--help"])

    assert result.exit_code == 0, result.stdout
    assert "Usage:" in result.stdout


def test_nested_composition_keeps_legacy_manager_patch_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yomiage import cli
    from yomiage.interfaces.cli import composition

    class PatchedManager(Exception):
        pass

    def patched_manager(*_args, **_kwargs):
        raise PatchedManager

    monkeypatch.setattr(cli, "_create_tts_manager", patched_manager)

    with pytest.raises(PatchedManager):
        composition.create_reading_engine({})
    with pytest.raises(PatchedManager):
        composition.create_news_scheduler({})
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-bot")
    monkeypatch.setenv("SLACK_APP_TOKEN", "test-app")
    with pytest.raises(PatchedManager):
        composition.create_slack_components({})


def test_nested_tuner_composition_keeps_legacy_profile_patch_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yomiage import cli
    from yomiage.interfaces.cli import composition

    class PatchedProfile(Exception):
        pass

    def patched_profile(*_args, **_kwargs):
        raise PatchedProfile

    monkeypatch.setattr(cli, "_load_profile", patched_profile)
    with pytest.raises(PatchedProfile):
        composition.create_tuner({}, "voice")

    monkeypatch.setattr(cli, "_load_voicepeak_profile", patched_profile)
    with pytest.raises(PatchedProfile):
        composition.create_voicepeak_tuner({}, "narrator")
