"""バッチ読み上げパイプラインの CLI コマンド。"""

from __future__ import annotations

import asyncio

import typer

from . import common

batch_app = typer.Typer(help="バッチ読み上げパイプライン")


@batch_app.command("analyze")
def batch_analyze(
    url: str = typer.Argument(help="小説のURL"),
    mode: str = typer.Option("voisona", "--mode", "-m", help="voisona / voicevox / voicepeak"),
    chapters: str | None = typer.Option(None, "--chapters", help="チャプター範囲 (例: 1-5)"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase A: 全文分析してマニフェスト生成."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_batch_engine(config, mode, output)
        manifest = await engine.analyze(url, chapter_range=common.parse_chapters(chapters))
        typer.echo(
            f"Analysis complete: {manifest.work_id}\n"
            f"  Title: {manifest.work_title}\n"
            f"  Sentences: {manifest.total_count}\n"
            f"  Characters: {len(manifest.characters)}"
        )

    asyncio.run(_run())


@batch_app.command("synthesize")
def batch_synthesize(
    work_id: str = typer.Argument(help="作品ID (manifest.jsonのwork_id)"),
    mode: str = typer.Option("voisona", "--mode", "-m", help="voisona / voicevox / voicepeak"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase B: マニフェストからバッチ合成."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_batch_engine(config, mode, output)
        manifest = await engine.synthesize(work_id)
        typer.echo(
            f"Synthesis {'complete' if manifest.synthesis_complete else 'partial'}: "
            f"{manifest.progress_str()}"
        )
        if manifest.failed_sentences:
            typer.echo(f"  Failed: {len(manifest.failed_sentences)}")

    asyncio.run(_run())


@batch_app.command("concat")
def batch_concat(
    work_id: str = typer.Argument(help="作品ID"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    fmt: str = typer.Option("wav", "--format", "-f", help="wav / mp3 / flac"),
    cleanup: bool = typer.Option(False, "--cleanup", help="結合後に個別ファイル削除"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase C: WAVファイル結合."""

    common.setup_logging(verbose)
    config = common.load_config()
    engine = common.create_batch_engine(config, output=output, fmt=fmt, cleanup=cleanup)
    result = engine.concat(work_id)
    if result:
        typer.echo(f"Output: {result}")
    else:
        typer.echo("No files to concatenate")


@batch_app.command("run")
def batch_run(
    url: str = typer.Argument(help="小説のURL"),
    mode: str = typer.Option("voisona", "--mode", "-m", help="voisona / voicevox / voicepeak"),
    chapters: str | None = typer.Option(None, "--chapters", help="チャプター範囲 (例: 1-5)"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    fmt: str = typer.Option("wav", "--format", "-f", help="wav / mp3 / flac"),
    cleanup: bool = typer.Option(False, "--cleanup", help="結合後に個別ファイル削除"),
    video: bool = typer.Option(False, "--video", help="動画も生成"),
    style: str = typer.Option("subtitle", "--style", "-s", help="subtitle / portrait"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """A + B + C (+ D) フルパイプライン."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_batch_engine(config, mode, output, fmt, cleanup)
        result = await engine.run(
            url,
            chapter_range=common.parse_chapters(chapters),
            video=video,
            style=style,
        )
        if result:
            typer.echo(f"Output: {result}")
        else:
            typer.echo("Pipeline completed but no output generated")

    asyncio.run(_run())


@batch_app.command("subtitle")
def batch_subtitle(
    work_id: str = typer.Argument(help="作品ID"),
    fmt: str = typer.Option("ass", "--format", "-f", help="ass / srt"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """字幕ファイル生成."""

    common.setup_logging(verbose)
    config = common.load_config()
    engine = common.create_batch_engine(config, output=output)
    results = engine.subtitle(work_id, fmt=fmt)
    if results:
        for chapter_index, path in sorted(results.items()):
            typer.echo(f"  Chapter {chapter_index + 1}: {path}")
    else:
        typer.echo("No subtitle files generated")


@batch_app.command("video")
def batch_video(
    work_id: str = typer.Argument(help="作品ID"),
    style: str = typer.Option("subtitle", "--style", "-s", help="subtitle / portrait"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase D: 動画生成."""

    common.setup_logging(verbose)
    config = common.load_config()
    engine = common.create_batch_engine(config, output=output)
    result = engine.video(work_id, style=style)
    if result:
        typer.echo(f"Video: {result}")
    else:
        typer.echo("No video generated")


@batch_app.command("status")
def batch_status(
    work_id: str = typer.Argument(help="作品ID"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """進捗表示."""

    common.setup_logging(verbose)
    config = common.load_config()
    engine = common.create_batch_engine(config, output=output)
    try:
        info = engine.status(work_id)
        typer.echo(
            f"Work: {info['work_id']}\n"
            f"  Title: {info['work_title']}\n"
            f"  Mode: {info['mode']}\n"
            f"  Chapters: {info['chapters']}\n"
            f"  Progress: {info['progress']}\n"
            f"  Pending: {info['pending']}\n"
            f"  Failed: {info['failed']}\n"
            f"  Analysis: {'done' if info['analysis_complete'] else 'incomplete'}\n"
            f"  Synthesis: {'done' if info['synthesis_complete'] else 'incomplete'}"
        )
    except FileNotFoundError:
        typer.echo(f"Manifest not found for work_id: {work_id}")


@batch_app.command("retry")
def batch_retry(
    work_id: str = typer.Argument(help="作品ID"),
    mode: str = typer.Option("voisona", "--mode", "-m", help="voisona / voicevox / voicepeak"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """失敗した文を再合成."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        engine = common.create_batch_engine(config, mode, output)
        manifest = await engine.synthesize(work_id, retry_failed=True)
        typer.echo(f"Retry result: {manifest.progress_str()}")
        if manifest.failed_sentences:
            typer.echo(f"  Still failed: {len(manifest.failed_sentences)}")

    asyncio.run(_run())


def register(app: typer.Typer) -> None:
    """root app に batch サブアプリを登録する。"""

    app.add_typer(batch_app, name="batch")


__all__ = [
    "batch_analyze",
    "batch_app",
    "batch_concat",
    "batch_retry",
    "batch_run",
    "batch_status",
    "batch_subtitle",
    "batch_synthesize",
    "batch_video",
    "register",
]
