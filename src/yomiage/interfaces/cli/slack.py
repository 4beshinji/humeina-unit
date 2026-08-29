"""Slack 監視 CLI。"""

from __future__ import annotations

import asyncio

import typer
from loguru import logger

from . import common


def slack_cmd(
    action: str = typer.Argument("start", help="start / stop"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Slack監視."""

    common.setup_logging(verbose)
    config = common.load_config()

    if action != "start":
        typer.echo(f"Unknown action: {action}")
        return

    async def _run() -> None:
        components = common.create_slack_components(config)
        if components is None:
            typer.echo("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set")
            return
        tts, monitor, scorer = components

        from ...tts.base import TTSParams

        async def on_message(msg) -> None:
            score = await scorer.score(msg)
            if scorer.is_important(score):
                logger.info(
                    f"Important Slack message: score={score:.2f}, chars={len(msg.text)}"
                )
                await tts.start()
                try:
                    await tts.enqueue(f"Slackメッセージです。{msg.text}", TTSParams())
                    await tts.drain()
                finally:
                    await tts.stop()

        monitor.on_message(on_message)
        try:
            await monitor.start()
        finally:
            await scorer.close()
            await tts.close()

    asyncio.run(_run())


def register(app: typer.Typer) -> None:
    """root app に Slack command を登録する。"""

    app.command("slack")(slack_cmd)


__all__ = ["register", "slack_cmd"]
