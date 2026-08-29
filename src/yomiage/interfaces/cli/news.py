"""ニュース読み上げ CLI。"""

from __future__ import annotations

import asyncio

import typer

from . import common


def news(
    action: str = typer.Argument("daily", help="daily / check"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="TTSプロバイダー"),
    output: str | None = typer.Option(None, "--output", "-o", help="音声ファイル出力先"),
    gemini_key: str | None = typer.Option(
        None,
        "--gemini-key",
        envvar="GEMINI_API_KEY",
        help="Gemini APIキー（Ollamaフォールバック用）",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """ニュース読み上げ."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        scheduler = common.create_news_scheduler(config, provider, gemini_key)
        try:
            if action == "daily":
                await scheduler.run_daily_summary(output_path=output)
            elif action == "check":
                await scheduler.check_urgent()
            else:
                typer.echo(f"Unknown action: {action}")
        finally:
            await scheduler.close()

    asyncio.run(_run())


def register(app: typer.Typer) -> None:
    """root app に news command を登録する。"""

    app.command()(news)


__all__ = ["news", "register"]
