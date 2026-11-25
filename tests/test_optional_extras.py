"""optional extra 不足時の案内と CLI 終了コード."""

from __future__ import annotations

import importlib.util

import pytest
from typer.testing import CliRunner

from yomiage import features
from yomiage.cli import app


def _hide_modules(monkeypatch: pytest.MonkeyPatch, *hidden: str) -> None:
    original = importlib.util.find_spec

    def fake_find_spec(name: str, *args, **kwargs):
        if name in hidden:
            return None
        return original(name, *args, **kwargs)

    monkeypatch.setattr(features.importlib.util, "find_spec", fake_find_spec)


def test_require_extra_passes_when_module_is_present() -> None:
    features.require_extra("dev", "pytest")


def test_require_extra_reports_extra_name_and_install_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hide_modules(monkeypatch, "feedparser")

    with pytest.raises(features.MissingExtraError) as error:
        features.require_extra("news", "feedparser")

    assert error.value.extra == "news"
    assert "humeina-unit[news]" in str(error.value)


@pytest.mark.parametrize(
    ("command", "module", "extra"),
    [(["news", "daily"], "feedparser", "news"), (["slack", "start"], "slack_sdk", "slack")],
)
def test_cli_exits_nonzero_with_guidance_when_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch, command: list[str], module: str, extra: str
) -> None:
    _hide_modules(monkeypatch, module)
    from yomiage.interfaces.cli import common

    def unexpected_config_load() -> dict:
        raise AssertionError("extra 不足時に設定や外部接続へ進まない")

    monkeypatch.setattr(common, "load_config", unexpected_config_load)

    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    assert f"humeina-unit[{extra}]" in result.output
