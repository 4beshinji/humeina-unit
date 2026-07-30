"""リアルタイム読み上げとボイス／キャラクター管理コマンド。"""

from __future__ import annotations

import asyncio
import json

import typer
from loguru import logger

from . import common

voices_app = typer.Typer(help="ボイス管理")
characters_app = typer.Typer(help="キャラクター管理")


def read(
    url: str = typer.Argument(help="読み上げるコンテンツのURL"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="TTSプロバイダー"),
    ex_voice: bool = typer.Option(False, "--ex-voice", help="EXボイスクリップを自動挿入"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
) -> None:
    """URLのコンテンツを読み上げる."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_reading_engine(config, provider, ex_voice=ex_voice)
        logger.info(f"Reading: {url}")
        try:
            await engine.read_url(url)
        except KeyboardInterrupt:
            engine.stop()
            logger.info("Reading interrupted")

    asyncio.run(_run())


def resume(
    provider: str | None = typer.Option(None, "--provider", "-p", help="TTSプロバイダー"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
) -> None:
    """最後のブックマークから再開."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_reading_engine(config, provider)
        await engine.resume_last()

    asyncio.run(_run())


@voices_app.command("list")
def voices_list(
    provider: str | None = typer.Option(None, "--provider", "-p"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """利用可能ボイス一覧."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tts = common.create_tts_manager(config, provider)
        voices = await tts.primary.list_voices()
        if not voices:
            typer.echo("No voices found (is the TTS engine running?)")
            return
        for voice in voices:
            voice_id = voice.get("id", voice.get("name", "?"))
            name = voice.get("name", voice.get("label", ""))
            typer.echo(f"  {voice_id}: {name}")

    asyncio.run(_run())


@characters_app.command("list")
def characters_list(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """現在の作品のキャラクター一覧."""

    common.setup_logging(verbose)
    from ...reader.character_db import DEFAULT_CHAR_DIR

    char_dir = DEFAULT_CHAR_DIR
    if not char_dir.exists():
        typer.echo("No character data found")
        return

    for path in sorted(char_dir.glob("*.json")):
        data = json.loads(path.read_text())
        typer.echo(f"\n[{path.stem}]")
        for name, char in data.items():
            voice = char.get("voice_id", "未割当")
            locked = " (locked)" if char.get("voice_locked") else ""
            typer.echo(f"  {name}: voice={voice}{locked}")


@characters_app.command("assign")
def characters_assign(
    name: str = typer.Argument(help="キャラクター名"),
    voice_id: str = typer.Argument(help="ボイスID"),
    work_id: str = typer.Option("", help="作品ID（省略時は最新）"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """手動ボイス割当."""

    common.setup_logging(verbose)
    from ...reader.character_db import DEFAULT_CHAR_DIR, CharacterDB

    if not work_id:
        files = sorted(
            DEFAULT_CHAR_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
        )
        if not files:
            typer.echo("No character data found")
            return
        work_id = files[0].stem

    db = CharacterDB(work_id)
    db.lock_voice(name, voice_id)
    typer.echo(f"Voice locked: {name} → {voice_id}")


def register(app: typer.Typer) -> None:
    """root app に reader 系 command を登録する。"""

    app.command()(read)
    app.command()(resume)
    app.add_typer(voices_app, name="voices")
    app.add_typer(characters_app, name="characters")


__all__ = [
    "characters_app",
    "characters_assign",
    "characters_list",
    "read",
    "register",
    "resume",
    "voices_app",
    "voices_list",
]
