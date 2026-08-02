"""台本から動画素材を生成する Studio CLI。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from . import common

studio_app = typer.Typer(help="動画作成アシスト（トークソフト動画素材生成）")


@studio_app.command("synth")
def studio_synth(
    script_file: Path = typer.Argument(help="台本ファイル (.txt/.csv/.json)"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="TTSプロバイダー"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    fmt: str = typer.Option("ymm4", "--format", "-f", help="ymm4 / plain"),
    pause: float = typer.Option(0.3, "--pause", help="セリフ間ポーズ秒"),
    speaker_map: Path | None = typer.Option(
        None, "--speaker-map", help="話者マッピングYAML/JSONパス"
    ),
    project: str | None = typer.Option(None, "--project", "-n", help="プロジェクト名"),
    no_cache: bool = typer.Option(False, "--no-cache", help="キャッシュ無効化"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """台本から音声素材をバッチ合成."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_studio_engine(config, Path(output))
        result = await engine.synth(
            script_path=script_file,
            speaker_map=speaker_map,
            provider=provider,
            output_format=fmt,
            default_pause=pause,
            project_name=project,
            no_cache=no_cache,
        )
        typer.echo(
            f"Complete: {len(result.results)} files\n"
            f"  Project: {result.name}\n"
            f"  Output: {result.output_dir}\n"
            f"  Duration: {sum(item.duration for item in result.results):.1f}s"
        )

    asyncio.run(_run())


@studio_app.command("preview")
def studio_preview(
    script_file: Path = typer.Argument(help="台本ファイル"),
    line: int = typer.Option(1, "--line", "-l", help="行番号 (1-based)"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="TTSプロバイダー"),
    speaker_map: Path | None = typer.Option(
        None, "--speaker-map", help="話者マッピングYAML/JSONパス"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """台本の指定行をプレビュー再生."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_studio_engine(config)
        await engine.preview(
            script_path=script_file,
            line_number=line,
            speaker_map=speaker_map,
            provider=provider,
        )

    asyncio.run(_run())


@studio_app.command("voices")
def studio_voices(
    provider: str | None = typer.Option(None, "--provider", "-p", help="プロバイダーでフィルター"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """利用可能なボイス一覧."""

    common.setup_logging(verbose)
    config = common.load_config()
    provider_names = {provider} if provider else {"voicevox", "voisona", "voicepeak"}
    providers = common.create_studio_providers(config, provider_names)

    async def _run() -> None:
        targets = [provider] if provider else ["voicevox", "voisona", "voicepeak"]
        for name in targets:
            prov = providers.get(name)
            if prov is None:
                typer.echo(f"Unknown provider: {name}")
                continue
            if not await prov.is_available():
                typer.echo(f"\n[{name}] (unavailable)")
                continue
            voices = await prov.list_voices()
            typer.echo(f"\n[{name}] ({len(voices)} voices)")
            for voice in voices:
                voice_id = voice.get("id", voice.get("name", "?"))
                voice_name = voice.get("name", voice.get("label", ""))
                typer.echo(f"  {voice_id}: {voice_name}")

    asyncio.run(_run())


def register(app: typer.Typer) -> None:
    """root app に studio サブアプリを登録する。"""

    app.add_typer(studio_app, name="studio")


__all__ = ["register", "studio_app", "studio_preview", "studio_synth", "studio_voices"]
