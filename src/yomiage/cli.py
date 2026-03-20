"""CLI entry point — typer application."""

import asyncio
import os
from pathlib import Path

import typer
from loguru import logger

app = typer.Typer(
    name="yomiage",
    help="高品質な音声読み上げシステム",
    no_args_is_help=True,
)


def _setup_logging(verbose: bool = False) -> None:
    import sys

    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<7} | {message}")


def _load_config() -> dict:
    from dotenv import load_dotenv

    load_dotenv()
    from .config import load_config

    return load_config()


def _create_tts_manager(config: dict, provider_override: str | None = None):
    from .config import get_tts_config
    from .tts.manager import TTSManager
    from .tts.voicepeak import VoicepeakProvider
    from .tts.voicevox import VoicevoxProvider
    from .tts.voisona import VoisonaProvider

    tts_cfg = config.get("tts", {})
    primary_name = provider_override or tts_cfg.get("primary_provider", "voicevox")
    fallback_name = tts_cfg.get("fallback_provider")
    lookahead = tts_cfg.get("lookahead_chunks", 3)

    providers = {
        "voisona": lambda: VoisonaProvider(get_tts_config(config, "voisona")),
        "voicevox": lambda: VoicevoxProvider(get_tts_config(config, "voicevox")),
        "voicepeak": lambda: VoicepeakProvider(get_tts_config(config, "voicepeak")),
    }

    primary = providers.get(primary_name, providers["voicevox"])()
    fallback = None
    if fallback_name and fallback_name != primary_name and fallback_name in providers:
        fallback = providers[fallback_name]()

    return TTSManager(primary=primary, fallback=fallback, lookahead=lookahead)


def _create_reading_engine(config: dict, provider_override: str | None = None):
    from .nlp.ollama_client import OllamaClient
    from .nlp.scene_analyzer import SceneAnalyzer
    from .nlp.splitter import TextSplitter
    from .reader.engine import ReadingEngine
    from .reader.param_mapper import ParamMapper

    tts = _create_tts_manager(config, provider_override)
    tts_cfg = config.get("tts", {})
    max_chars = tts_cfg.get("max_chunk_chars", 200)

    ollama_cfg = config.get("ollama", {})
    ollama = OllamaClient(
        url=ollama_cfg.get("url", "http://localhost:11434"),
        model=ollama_cfg.get("model", "qwen3.5:3b"),
    )

    scene_config_path = Path("config/scene_params.yaml")
    param_mapper = ParamMapper.from_config_file(scene_config_path)

    return ReadingEngine(
        tts_manager=tts,
        splitter=TextSplitter(max_chars=max_chars),
        scene_analyzer=SceneAnalyzer(ollama),
        param_mapper=param_mapper,
        auto_advance=config.get("reader", {}).get("auto_advance", True),
        lookahead_chunks=tts_cfg.get("lookahead_chunks", 5),
    )


@app.command()
def read(
    url: str = typer.Argument(help="読み上げるコンテンツのURL"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="TTSプロバイダー"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
) -> None:
    """URLのコンテンツを読み上げる."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        engine = _create_reading_engine(config, provider)
        logger.info(f"Reading: {url}")
        try:
            await engine.read_url(url)
        except KeyboardInterrupt:
            engine.stop()
            logger.info("Reading interrupted")

    asyncio.run(_run())


@app.command()
def resume(
    provider: str | None = typer.Option(None, "--provider", "-p", help="TTSプロバイダー"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
) -> None:
    """最後のブックマークから再開."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        engine = _create_reading_engine(config, provider)
        await engine.resume_last()

    asyncio.run(_run())


# --- Voice management ---

voices_app = typer.Typer(help="ボイス管理")
app.add_typer(voices_app, name="voices")


@voices_app.command("list")
def voices_list(
    provider: str | None = typer.Option(None, "--provider", "-p"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """利用可能ボイス一覧."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tts = _create_tts_manager(config, provider)
        voices = await tts.primary.list_voices()
        if not voices:
            typer.echo("No voices found (is the TTS engine running?)")
            return
        for v in voices:
            vid = v.get("id", v.get("name", "?"))
            name = v.get("name", v.get("label", ""))
            typer.echo(f"  {vid}: {name}")

    asyncio.run(_run())


# --- Characters ---

characters_app = typer.Typer(help="キャラクター管理")
app.add_typer(characters_app, name="characters")


@characters_app.command("list")
def characters_list(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """現在の作品のキャラクター一覧."""
    _setup_logging(verbose)

    from .reader.character_db import DEFAULT_CHAR_DIR

    char_dir = DEFAULT_CHAR_DIR
    if not char_dir.exists():
        typer.echo("No character data found")
        return

    import json

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
    _setup_logging(verbose)

    from .reader.character_db import DEFAULT_CHAR_DIR, CharacterDB

    if not work_id:
        files = sorted(
            DEFAULT_CHAR_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not files:
            typer.echo("No character data found")
            return
        work_id = files[0].stem

    db = CharacterDB(work_id)
    db.lock_voice(name, voice_id)
    typer.echo(f"Voice locked: {name} → {voice_id}")


# --- News ---


@app.command()
def news(
    action: str = typer.Argument("daily", help="daily / check"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """ニュース読み上げ."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        from .news.scheduler import NewsScheduler
        from .nlp.ollama_client import OllamaClient

        tts = _create_tts_manager(config)
        ollama_cfg = config.get("ollama", {})
        ollama = OllamaClient(
            url=ollama_cfg.get("url", "http://localhost:11434"),
            model=ollama_cfg.get("summary_model") or ollama_cfg.get("model", "qwen3.5:3b"),
        )

        news_cfg = config.get("news", {})
        news_tts = news_cfg.get("tts", {})
        scheduler = NewsScheduler(
            tts_manager=tts,
            ollama=ollama,
            sources=news_cfg.get("sources"),
            urgency_threshold=news_cfg.get("urgency_threshold", 0.8),
            tts_speed=news_tts.get("speed", 1.0),
        )

        if action == "daily":
            await scheduler.run_daily_summary()
        elif action == "check":
            await scheduler.check_urgent()
        else:
            typer.echo(f"Unknown action: {action}")

    asyncio.run(_run())


# --- Slack ---


@app.command("slack")
def slack_cmd(
    action: str = typer.Argument("start", help="start / stop"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Slack監視."""
    _setup_logging(verbose)
    config = _load_config()

    if action != "start":
        typer.echo(f"Unknown action: {action}")
        return

    async def _run():
        from .nlp.ollama_client import OllamaClient
        from .slack.monitor import SlackMonitor
        from .slack.scorer import ImportanceScorer
        from .tts.base import TTSParams

        tts = _create_tts_manager(config)
        slack_cfg = config.get("slack", {})
        ollama_cfg = config.get("ollama", {})

        bot_token = os.getenv("SLACK_BOT_TOKEN", "")
        app_token = os.getenv("SLACK_APP_TOKEN", "")
        if not bot_token or not app_token:
            typer.echo("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set")
            return

        ollama = OllamaClient(
            url=ollama_cfg.get("url", "http://localhost:11434"),
            model=ollama_cfg.get("model", "qwen3.5:3b"),
        )
        scorer = ImportanceScorer(
            ollama=ollama,
            mention_boost=slack_cfg.get("mention_boost", 0.5),
            threshold=slack_cfg.get("importance_threshold", 0.6),
        )
        monitor = SlackMonitor(
            bot_token=bot_token,
            app_token=app_token,
            channels=slack_cfg.get("channels", []),
        )

        async def on_message(msg):
            score = await scorer.score(msg)
            if scorer.is_important(score):
                logger.info(f"Important Slack message (score={score:.2f}): {msg.text[:50]}")
                await tts.start()
                try:
                    await tts.enqueue(
                        f"Slackメッセージです。{msg.text}", TTSParams()
                    )
                    await tts.enqueue("", TTSParams())
                finally:
                    await tts.stop()

        monitor.on_message(on_message)
        await monitor.start()

    asyncio.run(_run())


# --- Batch ---

batch_app = typer.Typer(help="バッチ読み上げパイプライン")
app.add_typer(batch_app, name="batch")


def _parse_chapters(chapters: str | None) -> tuple[int, int] | None:
    if not chapters:
        return None
    parts = chapters.split("-")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return int(parts[0]), int(parts[0])


def _create_batch_engine(
    config: dict,
    mode: str | None = None,
    output: str | None = None,
    fmt: str | None = None,
    cleanup: bool = False,
):
    from .batch.engine import BatchEngine

    batch_cfg = config.get("batch", {})
    return BatchEngine(
        config=config,
        mode=mode or batch_cfg.get("default_mode", "voisona"),
        output_dir=output or batch_cfg.get("output_dir", "output"),
        output_format=fmt or batch_cfg.get("concat_format", "wav"),
        cleanup=cleanup or batch_cfg.get("cleanup_after_concat", False),
    )


@batch_app.command("analyze")
def batch_analyze(
    url: str = typer.Argument(help="小説のURL"),
    mode: str = typer.Option("voisona", "--mode", "-m", help="voisona / voicevox / voicepeak"),
    chapters: str | None = typer.Option(None, "--chapters", help="チャプター範囲 (例: 1-5)"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase A: 全文分析してマニフェスト生成."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        engine = _create_batch_engine(config, mode, output)
        manifest = await engine.analyze(url, chapter_range=_parse_chapters(chapters))
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        engine = _create_batch_engine(config, mode, output)
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
    _setup_logging(verbose)
    config = _load_config()

    engine = _create_batch_engine(config, output=output, fmt=fmt, cleanup=cleanup)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        engine = _create_batch_engine(config, mode, output, fmt, cleanup)
        result = await engine.run(
            url, chapter_range=_parse_chapters(chapters),
            video=video, style=style,
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
    _setup_logging(verbose)
    config = _load_config()

    engine = _create_batch_engine(config, output=output)
    results = engine.subtitle(work_id, fmt=fmt)
    if results:
        for ch_index, path in sorted(results.items()):
            typer.echo(f"  Chapter {ch_index + 1}: {path}")
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
    _setup_logging(verbose)
    config = _load_config()

    engine = _create_batch_engine(config, output=output)
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
    _setup_logging(verbose)
    config = _load_config()

    engine = _create_batch_engine(config, output=output)
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


# --- Tune ---

tune_app = typer.Typer(help="ボイスプロファイル チューニングツール")
app.add_typer(tune_app, name="tune")


def _load_profile(voice_name: str):
    from .tools.voice_profile import VoiceProfile

    profile = VoiceProfile.find(voice_name)
    if profile:
        return profile

    # Not found — create default template
    typer.echo(f"Profile not found for '{voice_name}', creating default template")
    profile = VoiceProfile.create_default(voice_name, voice_name)
    return profile


def _profile_path(voice_name: str) -> "Path":
    """Derive YAML path for a voice profile."""
    # Strip language suffix for filename
    stem = voice_name.replace("_ja_JP", "").replace("_", "-")
    return Path(f"config/voice_profiles/{stem}.yaml")


def _create_tuner(config: dict, voice_name: str, text: str | None = None):
    from .tools.tuner import DEFAULT_TEST_TEXT, create_tuner_from_config

    profile = _load_profile(voice_name)
    return create_tuner_from_config(
        config, profile, test_text=text or DEFAULT_TEST_TEXT
    ), profile


@tune_app.command("range")
def tune_range(
    voice_name: str = typer.Argument(help="ボイス名 (例: nurse-robot-type-t_ja_JP)"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 1: パラメータ実用範囲を探索."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_tuner(config, voice_name, text)
        await tuner.explore_range()
        path = _profile_path(voice_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_tuner(config, voice_name, text)
        await tuner.create_preset()
        path = _profile_path(voice_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_tuner(config, voice_name, text)
        await tuner.tune_emotion(base_preset=base)
        path = _profile_path(voice_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_tuner(config, voice_name, text)
        await tuner.calibrate_noise(base_preset=base)
        path = _profile_path(voice_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, _profile = _create_tuner(config, voice_name, text)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, _profile = _create_tuner(config, voice_name, text)
        await tuner.test_single(
            preset=preset, emotion=emotion, intensity=intensity, text=text
        )

    asyncio.run(_run())


# --- VOICEPEAK Tune ---

voicepeak_tune_app = typer.Typer(help="VOICEPEAK ボイスプロファイル チューニングツール")
app.add_typer(voicepeak_tune_app, name="vp-tune")


def _load_voicepeak_profile(narrator_name: str):
    from .tools.voicepeak_profile import VoicepeakVoiceProfile

    profile = VoicepeakVoiceProfile.find(narrator_name)
    if profile:
        return profile

    typer.echo(f"Profile not found for '{narrator_name}', creating default template")
    profile = VoicepeakVoiceProfile.create_default(narrator_name, narrator_name)
    return profile


def _voicepeak_profile_path(narrator_name: str) -> "Path":
    """Derive YAML path for a voicepeak profile."""
    stem = narrator_name.replace(" ", "-").lower()
    return Path(f"config/voicepeak_profiles/{stem}.yaml")


def _create_voicepeak_tuner(config: dict, narrator_name: str, text: str | None = None):
    from .tools.voicepeak_tuner import (
        DEFAULT_TEST_TEXT,
        create_voicepeak_tuner_from_config,
    )

    profile = _load_voicepeak_profile(narrator_name)
    return create_voicepeak_tuner_from_config(
        config, profile, test_text=text or DEFAULT_TEST_TEXT
    ), profile


@voicepeak_tune_app.command("range")
def vp_tune_range(
    narrator_name: str = typer.Argument(help="ナレーター名"),
    text: str | None = typer.Option(None, "--text", "-t", help="テスト文"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Phase 1: パラメータ実用範囲を探索."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_voicepeak_tuner(config, narrator_name, text)
        await tuner.explore_range()
        path = _voicepeak_profile_path(narrator_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_voicepeak_tuner(config, narrator_name, text)
        await tuner.create_preset()
        path = _voicepeak_profile_path(narrator_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_voicepeak_tuner(config, narrator_name, text)
        await tuner.tune_emotion(base_preset=base)
        path = _voicepeak_profile_path(narrator_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, profile = _create_voicepeak_tuner(config, narrator_name, text)
        await tuner.calibrate_noise(base_preset=base)
        path = _voicepeak_profile_path(narrator_name)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, _profile = _create_voicepeak_tuner(config, narrator_name, text)
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
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        tuner, _profile = _create_voicepeak_tuner(config, narrator_name, text)
        await tuner.test_single(
            preset=preset, emotion=emotion, intensity=intensity, text=text
        )

    asyncio.run(_run())


@batch_app.command("retry")
def batch_retry(
    work_id: str = typer.Argument(help="作品ID"),
    mode: str = typer.Option("voisona", "--mode", "-m", help="voisona / voicevox / voicepeak"),
    output: str = typer.Option("output", "--output", "-o", help="出力ディレクトリ"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """失敗した文を再合成."""
    _setup_logging(verbose)
    config = _load_config()

    async def _run():
        engine = _create_batch_engine(config, mode, output)
        manifest = await engine.synthesize(work_id, retry_failed=True)
        typer.echo(f"Retry result: {manifest.progress_str()}")
        if manifest.failed_sentences:
            typer.echo(f"  Still failed: {len(manifest.failed_sentences)}")

    asyncio.run(_run())


# --- Server ---


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="ホスト"),
    port: int = typer.Option(8030, help="ポート"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """FastAPIサーバー起動."""
    _setup_logging(verbose)
    import uvicorn

    uvicorn.run(
        "yomiage.server:app",
        host=host,
        port=port,
        log_level="debug" if verbose else "info",
    )


if __name__ == "__main__":
    app()
