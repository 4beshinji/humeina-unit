"""serve CLIの安全なbind既定値テスト."""

from __future__ import annotations

from typer.testing import CliRunner

from yomiage.cli import app


def test_serve_defaults_to_loopback(monkeypatch) -> None:
    """引数なしserveはloopbackへbindする."""
    calls: list[dict] = []
    monkeypatch.delenv("YOMIAGE_API_TOKEN", raising=False)
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *args, **kwargs: calls.append({"args": args, **kwargs}),
    )

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0, result.stdout
    assert calls
    assert calls[0]["host"] == "127.0.0.1"
    assert "loopback" in result.output.lower()
    assert "token" not in result.output.lower()


def test_serve_rejects_non_loopback_without_token(monkeypatch) -> None:
    """非loopback bindはtokenなしで起動前に拒否する."""
    monkeypatch.delenv("YOMIAGE_API_TOKEN", raising=False)

    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert "API token" in result.output
    assert "uvicorn" not in result.output.lower()


def test_serve_allows_non_loopback_with_token(monkeypatch) -> None:
    """API tokenを伴う非loopback bindはAPI-002で許可される."""
    calls: list[dict] = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *args, **kwargs: calls.append({"args": args, **kwargs}),
    )
    result = CliRunner().invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--api-token", "do-not-log-this"],
    )

    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0]["host"] == "0.0.0.0"
    assert "authenticated external" in result.output
    assert "do-not-log-this" not in result.output
