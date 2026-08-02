"""ボイスプロファイル・チューニング CLI。"""

from __future__ import annotations

import asyncio

import typer

from . import common

tune_app = typer.Typer(help="ボイスプロファイル チューニングツール")
voicepeak_tune_app = typer.Typer(help="VOICEPEAK ボイスプロファイル チューニングツール")


@tune_app.command("range")
def tune_range(
    voice_name: str = typer.Argument(help="ボイス名 (例: nurse-robot-type-t_ja_JP)"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 1: パラメータ実用範囲を探索."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_tuner(config, voice_name, text)
        await tuner.explore_range()
        path = common.profile_path(voice_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@tune_app.command("preset")
def tune_preset(
    voice_name: str = typer.Argument(help="ボイス名"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 2: アーキタイププリセット作成."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_tuner(config, voice_name, text)
        await tuner.create_preset()
        path = common.profile_path(voice_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@tune_app.command("emotion")
def tune_emotion(
    voice_name: str = typer.Argument(help="ボイス名"),
    base: str = typer.Option("female_young", "--base", "-b", help="ベースプリセット"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 3: 感情マスク調整."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_tuner(config, voice_name, text)
        await tuner.tune_emotion(base_preset=base)
        path = common.profile_path(voice_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@tune_app.command("noise")
def tune_noise(
    voice_name: str = typer.Argument(help="ボイス名"),
    base: str = typer.Option("female_young", "--base", "-b", help="ベースプリセット"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 4: ノイズ調整."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_tuner(config, voice_name, text)
        await tuner.calibrate_noise(base_preset=base)
        path = common.profile_path(voice_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@tune_app.command("demo")
def tune_demo(
    voice_name: str = typer.Argument(help="ボイス名"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 5: 全プリセット × 全感情デモ再生."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, _profile = common.create_tuner(config, voice_name, text)
        await tuner.demo(text=text)

    asyncio.run(_run())


@tune_app.command("test")
def tune_test(
    voice_name: str = typer.Argument(help="ボイス名"),
    preset: str = typer.Argument(help="プリセット名 (例: male_young)"),
    emotion: str = typer.Option("neutral", "--emotion", "-e", help="感情"),
    intensity: float = typer.Option(0.7, "--intensity", "-i", help="感情強度 (0.0-1.0)"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """単発プリセット × 感情テスト."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, _profile = common.create_tuner(config, voice_name, text)
        await tuner.test_single(
            preset=preset,
            emotion=emotion,
            intensity=intensity,
            text=text,
        )

    asyncio.run(_run())


@voicepeak_tune_app.command("range")
def vp_tune_range(
    narrator_name: str = typer.Argument(help="ナレーター名"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 1: パラメータ実用範囲を探索."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_voicepeak_tuner(config, narrator_name, text)
        await tuner.explore_range()
        path = common.voicepeak_profile_path(narrator_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@voicepeak_tune_app.command("preset")
def vp_tune_preset(
    narrator_name: str = typer.Argument(help="ナレーター名"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 2: アーキタイププリセット作成."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_voicepeak_tuner(config, narrator_name, text)
        await tuner.create_preset()
        path = common.voicepeak_profile_path(narrator_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@voicepeak_tune_app.command("emotion")
def vp_tune_emotion(
    narrator_name: str = typer.Argument(help="ナレーター名"),
    base: str = typer.Option("female_young", "--base", "-b", help="ベースプリセット"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 3: 感情パラメータ調整."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_voicepeak_tuner(config, narrator_name, text)
        await tuner.tune_emotion(base_preset=base)
        path = common.voicepeak_profile_path(narrator_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@voicepeak_tune_app.command("noise")
def vp_tune_noise(
    narrator_name: str = typer.Argument(help="ナレーター名"),
    base: str = typer.Option("female_young", "--base", "-b", help="ベースプリセット"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 4: ノイズ調整."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, profile = common.create_voicepeak_tuner(config, narrator_name, text)
        await tuner.calibrate_noise(base_preset=base)
        path = common.voicepeak_profile_path(narrator_name)
        profile.save(path)
        typer.echo(f"Profile saved: {path}")

    asyncio.run(_run())


@voicepeak_tune_app.command("demo")
def vp_tune_demo(
    narrator_name: str = typer.Argument(help="ナレーター名"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 5: 全プリセット × 全感情デモ再生."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, _profile = common.create_voicepeak_tuner(config, narrator_name, text)
        await tuner.demo(text=text)

    asyncio.run(_run())


@voicepeak_tune_app.command("test")
def vp_tune_test(
    narrator_name: str = typer.Argument(help="ナレーター名"),
    preset: str = typer.Argument(help="プリセット名 (例: male_young)"),
    emotion: str = typer.Option("neutral", "--emotion", "-e", help="感情"),
    intensity: float = typer.Option(0.7, "--intensity", "-i", help="感情強度 (0.0-1.0)"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """単発プリセット × 感情テスト."""

    common.setup_logging(verbose)
    config = common.load_config()

    async def _run() -> None:
        tuner, _profile = common.create_voicepeak_tuner(config, narrator_name, text)
        await tuner.test_single(
            preset=preset,
            emotion=emotion,
            intensity=intensity,
            text=text,
        )

    asyncio.run(_run())


def register(app: typer.Typer) -> None:
    """root app に tune サブアプリを登録する。"""

    app.add_typer(tune_app, name="tune")
    app.add_typer(voicepeak_tune_app, name="vp-tune")


__all__ = [
    "register",
    "tune_app",
    "tune_demo",
    "tune_emotion",
    "tune_noise",
    "tune_preset",
    "tune_range",
    "tune_test",
    "voicepeak_tune_app",
    "vp_tune_demo",
    "vp_tune_emotion",
    "vp_tune_noise",
    "vp_tune_preset",
    "vp_tune_range",
    "vp_tune_test",
]
