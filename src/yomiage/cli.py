"""CLI entry point — Typer application composition.

実際の command 定義は ``yomiage.interfaces.cli`` 配下に分割されています。この module
は既存の ``yomiage.cli:app`` エントリポイントと private composition helper を保つ薄い
compatibility facade です。
"""

from __future__ import annotations

from pathlib import Path

import typer

from .interfaces.cli import batch as _batch
from .interfaces.cli import composition as _composition
from .interfaces.cli import news as _news
from .interfaces.cli import reader as _reader
from .interfaces.cli import server as _server
from .interfaces.cli import slack as _slack
from .interfaces.cli import studio as _studio
from .interfaces.cli import tuning as _tuning

app = typer.Typer(
    name="yomiage",
    help="高品質な音声読み上げシステム",
    no_args_is_help=True,
)


# --- Compatibility composition helpers ---------------------------------

def _setup_logging(verbose: bool = False) -> None:
    _composition.setup_logging(verbose)


def _load_config() -> dict:
    return _composition.load_config()


def _create_tts_manager(config: dict, provider_override: str | None = None):
    return _composition.create_tts_manager(config, provider_override)


def _create_reading_engine(
    config: dict,
    provider_override: str | None = None,
    ex_voice: bool = False,
):
    return _composition.create_reading_engine(config, provider_override, ex_voice=ex_voice)


def _create_batch_engine(
    config: dict,
    mode: str | None = None,
    output: str | None = None,
    fmt: str | None = None,
    cleanup: bool = False,
):
    return _composition.create_batch_engine(config, mode, output, fmt, cleanup)


def _parse_chapters(chapters: str | None) -> tuple[int, int] | None:
    return _composition.parse_chapters(chapters)


def _load_profile(voice_name: str):
    return _composition.load_profile(voice_name)


def _profile_path(voice_name: str) -> Path:
    return _composition.profile_path(voice_name)


def _create_tuner(config: dict, voice_name: str, text: str | None = None):
    return _composition.create_tuner(config, voice_name, text)


def _load_voicepeak_profile(narrator_name: str):
    return _composition.load_voicepeak_profile(narrator_name)


def _voicepeak_profile_path(narrator_name: str) -> Path:
    return _composition.voicepeak_profile_path(narrator_name)


def _create_voicepeak_tuner(config: dict, narrator_name: str, text: str | None = None):
    return _composition.create_voicepeak_tuner(config, narrator_name, text)


def _create_studio_engine(config: dict, output_dir: Path | None = None):
    return _composition.create_studio_engine(config, output_dir)


def _create_studio_providers(config: dict, provider_names: set[str]):
    return _composition.create_studio_providers(config, provider_names)


def _create_news_scheduler(
    config: dict, provider: str | None = None, gemini_key: str | None = None,
):
    return _composition.create_news_scheduler(config, provider, gemini_key)


def _create_slack_components(config: dict):
    return _composition.create_slack_components(config)


def _is_loopback_host(host: str) -> bool:
    return _server.is_loopback_host(host)


def _validate_server_bind(host: str, api_token: str | None) -> None:
    _server.validate_server_bind(host, api_token)


# --- Public command/function compatibility exports ----------------------

read = _reader.read
resume = _reader.resume
voices_list = _reader.voices_list
characters_list = _reader.characters_list
characters_assign = _reader.characters_assign
voices_app = _reader.voices_app
characters_app = _reader.characters_app

news = _news.news
slack_cmd = _slack.slack_cmd

batch_app = _batch.batch_app
batch_analyze = _batch.batch_analyze
batch_synthesize = _batch.batch_synthesize
batch_concat = _batch.batch_concat
batch_run = _batch.batch_run
batch_subtitle = _batch.batch_subtitle
batch_video = _batch.batch_video
batch_status = _batch.batch_status
batch_retry = _batch.batch_retry

tune_app = _tuning.tune_app
tune_range = _tuning.tune_range
tune_preset = _tuning.tune_preset
tune_emotion = _tuning.tune_emotion
tune_noise = _tuning.tune_noise
tune_demo = _tuning.tune_demo
tune_test = _tuning.tune_test

voicepeak_tune_app = _tuning.voicepeak_tune_app
vp_tune_range = _tuning.vp_tune_range
vp_tune_preset = _tuning.vp_tune_preset
vp_tune_emotion = _tuning.vp_tune_emotion
vp_tune_noise = _tuning.vp_tune_noise
vp_tune_demo = _tuning.vp_tune_demo
vp_tune_test = _tuning.vp_tune_test

studio_app = _studio.studio_app
studio_synth = _studio.studio_synth
studio_preview = _studio.studio_preview
studio_voices = _studio.studio_voices

serve = _server.serve


_reader.register(app)
_news.register(app)
_slack.register(app)
_batch.register(app)
_tuning.register(app)
_studio.register(app)
_server.register(app)


if __name__ == "__main__":  # pragma: no cover - module execution convenience
    app()


__all__ = [
    "app",
    "batch_analyze",
    "batch_app",
    "batch_concat",
    "batch_retry",
    "batch_run",
    "batch_status",
    "batch_subtitle",
    "batch_synthesize",
    "batch_video",
    "characters_app",
    "characters_assign",
    "characters_list",
    "news",
    "read",
    "resume",
    "serve",
    "slack_cmd",
    "studio_app",
    "studio_preview",
    "studio_synth",
    "studio_voices",
    "tune_app",
    "tune_demo",
    "tune_emotion",
    "tune_noise",
    "tune_preset",
    "tune_range",
    "tune_test",
    "voicepeak_tune_app",
    "voices_app",
    "voices_list",
    "vp_tune_demo",
    "vp_tune_emotion",
    "vp_tune_noise",
    "vp_tune_preset",
    "vp_tune_range",
    "vp_tune_test",
]
