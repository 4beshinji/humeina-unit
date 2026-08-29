"""CLI composition-root implementations.

このモジュールは command 定義から分離された構築境界です。具象 provider や NLP client
の生成はここ、または ``yomiage.bootstrap`` に限定します。
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from loguru import logger


def setup_logging(verbose: bool = False) -> None:
    import sys

    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<7} | {message}")


def load_config() -> dict:
    from dotenv import load_dotenv

    load_dotenv()
    from ...config import load_config as load_yaml_config

    return load_yaml_config()


def create_tts_manager(config: dict, provider_override: str | None = None):
    from ...bootstrap import create_tts_manager as bootstrap_create_tts_manager

    return bootstrap_create_tts_manager(config, provider_override)


def create_reading_engine(
    config: dict,
    provider_override: str | None = None,
    *,
    ex_voice: bool = False,
):
    """Reader 用の構成を組み立てる（旧 ``cli._create_reading_engine`` 互換）。"""

    from ...llm.factory import create_llm_service
    from ...nlp.scene_analyzer import SceneAnalyzer
    from ...nlp.splitter import TextSplitter
    from ...reader.engine import ReadingEngine
    from ...reader.param_mapper import ParamMapper
    from .common import _compat_helper

    tts = _compat_helper("_create_tts_manager")(config, provider_override)
    tts_cfg = config.get("tts", {})
    max_chars = tts_cfg.get("max_chunk_chars", 200)

    llm = create_llm_service(config)

    scene_params = config.get("scene_params")
    param_mapper = (
        ParamMapper(scene_params)
        if isinstance(scene_params, dict)
        else ParamMapper.from_config_file()
    )

    ex_voice_manager = None
    if ex_voice:
        from ...exvoice.catalog import load_catalog
        from ...exvoice.manager import ExVoiceManager
        from ...exvoice.selector import ExVoiceSelector

        ev_cfg = config.get("ex_voice", {})
        wav_dir = Path(ev_cfg.get("wav_dir", "/home/sin/code/una/VoiceWav"))
        catalog = load_catalog(wav_dir)
        selector = ExVoiceSelector(llm, catalog)
        ex_voice_manager = ExVoiceManager(
            catalog=catalog,
            selector=selector,
            cooldown_chunks=ev_cfg.get("cooldown_chunks", 10),
            max_per_chapter=ev_cfg.get("max_per_chapter", 8),
            llm_max_insertions=ev_cfg.get("llm_max_insertions", 2),
        )
        logger.info(f"EX voice enabled: {len(catalog)} clips from {wav_dir}")

    batch_cfg = config.get("batch", {})
    engine = ReadingEngine(
        tts_manager=tts,
        splitter=TextSplitter(max_chars=max_chars),
        scene_analyzer=SceneAnalyzer(llm),
        param_mapper=param_mapper,
        auto_advance=config.get("reader", {}).get("auto_advance", True),
        lookahead_chunks=tts_cfg.get("lookahead_chunks", 5),
        vm_mount=batch_cfg.get("voisona_vm_mount", "Z:"),
        output_dir=batch_cfg.get("output_dir", "output"),
        synth_concurrency=batch_cfg.get("synth_concurrency", 2),
        ex_voice=ex_voice_manager,
    )
    engine._owns_llm = True
    return engine


def create_batch_engine(
    config: dict,
    mode: str | None = None,
    output: str | None = None,
    fmt: str | None = None,
    cleanup: bool = False,
):
    from ...batch.engine import BatchEngine

    batch_cfg = config.get("batch", {})
    return BatchEngine(
        config=config,
        mode=mode or batch_cfg.get("default_mode", "voisona"),
        output_dir=output or batch_cfg.get("output_dir", "output"),
        output_format=fmt or batch_cfg.get("concat_format", "wav"),
        cleanup=cleanup or batch_cfg.get("cleanup_after_concat", False),
    )


def parse_chapters(chapters: str | None) -> tuple[int, int] | None:
    if not chapters:
        return None
    parts = chapters.split("-")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return int(parts[0]), int(parts[0])


def load_profile(voice_name: str):
    from ...tools.voice_profile import VoiceProfile

    profile = VoiceProfile.find(voice_name)
    if profile:
        return profile
    typer.echo(f"Profile not found for '{voice_name}', creating default template")
    return VoiceProfile.create_default(voice_name, voice_name)


def profile_path(voice_name: str) -> Path:
    stem = voice_name.replace("_ja_JP", "").replace("_", "-")
    return Path(f"config/voice_profiles/{stem}.yaml")


def create_tuner(config: dict, voice_name: str, text: str | None = None):
    from ...tools.tuner import DEFAULT_TEST_TEXT, create_tuner_from_config
    from .common import _compat_helper

    profile = _compat_helper("_load_profile")(voice_name)
    return create_tuner_from_config(
        config, profile, test_text=text or DEFAULT_TEST_TEXT
    ), profile


def load_voicepeak_profile(narrator_name: str):
    from ...tools.voicepeak_profile import VoicepeakVoiceProfile

    profile = VoicepeakVoiceProfile.find(narrator_name)
    if profile:
        return profile
    typer.echo(f"Profile not found for '{narrator_name}', creating default template")
    return VoicepeakVoiceProfile.create_default(narrator_name, narrator_name)


def voicepeak_profile_path(narrator_name: str) -> Path:
    stem = narrator_name.replace(" ", "-").lower()
    return Path(f"config/voicepeak_profiles/{stem}.yaml")


def create_voicepeak_tuner(config: dict, narrator_name: str, text: str | None = None):
    from ...tools.voicepeak_tuner import (
        DEFAULT_TEST_TEXT,
        create_voicepeak_tuner_from_config,
    )
    from .common import _compat_helper

    profile = _compat_helper("_load_voicepeak_profile")(narrator_name)
    return create_voicepeak_tuner_from_config(
        config, profile, test_text=text or DEFAULT_TEST_TEXT
    ), profile


def create_studio_engine(config: dict, output_dir: Path | None = None):
    from ...studio.engine import StudioEngine

    if output_dir is not None:
        return StudioEngine(config, output_dir=output_dir)
    return StudioEngine(config)


def create_studio_providers(config: dict, provider_names: set[str]):
    from ...bootstrap import create_studio_providers as bootstrap_create_studio_providers

    return bootstrap_create_studio_providers(config, provider_names)


def create_news_scheduler(
    config: dict, provider: str | None = None, gemini_key: str | None = None,
):
    from ...llm.factory import create_llm_service
    from ...news.scheduler import NewsScheduler
    from .common import _compat_helper

    tts = _compat_helper("_create_tts_manager")(config, provider)
    llm_config = config
    if gemini_key:
        from copy import deepcopy

        from ...llm.factory import normalize_llm_config

        llm_config = deepcopy(config)
        normalized = normalize_llm_config(config)
        normalized.setdefault("providers", {})["gemini"] = {
            "type": "gemini",
            "model": "gemini-2.0-flash",
            "api_key": gemini_key,
        }
        normalized.setdefault("fallback", {}).setdefault("order", []).append("gemini")
        llm_config["llm"] = normalized
        logger.info("Gemini API fallback enabled")
    llm = create_llm_service(llm_config)
    news_cfg = config.get("news", {})
    news_tts = news_cfg.get("tts", {})
    return NewsScheduler(
        tts_manager=tts,
        llm=llm,
        sources=news_cfg.get("sources"),
        urgency_threshold=news_cfg.get("urgency_threshold", 0.8),
        tts_speed=news_tts.get("speed", 1.0),
        owns_llm=True,
    )


def create_slack_components(config: dict):
    slack_cfg = config.get("slack", {})
    bot_token = os.getenv("SLACK_BOT_TOKEN", "")
    app_token = os.getenv("SLACK_APP_TOKEN", "")
    if not bot_token or not app_token:
        return None

    from ...llm.factory import create_llm_service
    from ...slack.monitor import SlackMonitor
    from ...slack.scorer import ImportanceScorer
    from .common import _compat_helper

    tts = _compat_helper("_create_tts_manager")(config)
    llm = create_llm_service(config)
    scorer = ImportanceScorer(
        llm=llm,
        mention_boost=slack_cfg.get("mention_boost", 0.5),
        threshold=slack_cfg.get("importance_threshold", 0.6),
        owns_llm=True,
    )
    monitor = SlackMonitor(
        bot_token=bot_token,
        app_token=app_token,
        channels=slack_cfg.get("channels", []),
    )
    return tts, monitor, scorer


__all__ = [
    "create_batch_engine",
    "create_news_scheduler",
    "create_reading_engine",
    "create_slack_components",
    "create_studio_engine",
    "create_studio_providers",
    "create_tts_manager",
    "create_tuner",
    "create_voicepeak_tuner",
    "load_config",
    "parse_chapters",
    "profile_path",
    "setup_logging",
    "voicepeak_profile_path",
]
