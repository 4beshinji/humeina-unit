"""CLI command helpers.

``yomiage.cli`` に残る既存の private helper を実行時に解決することで、古い利用者が
``_load_config`` や ``_create_batch_engine`` を差し替える互換性を保つ。実際の構築処理
は composition root に置き、command module から具象 TTS provider を直接生成しない。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import typer

from ...features import MissingExtraError
from ...features import require_extra as _require_extra


def _compat_helper(name: str) -> Callable[..., Any]:
    """``yomiage.cli`` の互換 helper を遅延解決する。"""

    module = sys.modules.get("yomiage.cli")
    if module is None:
        from ... import cli as module
    return getattr(module, name)


def require_extra(extra: str, *modules: str) -> None:
    """optional extra が無ければ導入方法を表示して終了コード 1 で終える。"""

    try:
        _require_extra(extra, *modules)
    except MissingExtraError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def setup_logging(verbose: bool = False) -> None:
    _compat_helper("_setup_logging")(verbose)


def load_config() -> dict:
    return _compat_helper("_load_config")()


def create_tts_manager(config: Mapping[str, Any], provider: str | None = None):
    return _compat_helper("_create_tts_manager")(config, provider)


def create_reading_engine(
    config: Mapping[str, Any],
    provider: str | None = None,
    *,
    ex_voice: bool = False,
):
    return _compat_helper("_create_reading_engine")(config, provider, ex_voice=ex_voice)


def create_batch_engine(
    config: Mapping[str, Any],
    mode: str | None = None,
    output: str | None = None,
    fmt: str | None = None,
    cleanup: bool = False,
):
    return _compat_helper("_create_batch_engine")(config, mode, output, fmt, cleanup)


def parse_chapters(chapters: str | None) -> tuple[int, int] | None:
    return _compat_helper("_parse_chapters")(chapters)


def create_tuner(config: Mapping[str, Any], voice_name: str, text: str | None = None):
    return _compat_helper("_create_tuner")(config, voice_name, text)


def profile_path(voice_name: str) -> Path:
    return _compat_helper("_profile_path")(voice_name)


def create_voicepeak_tuner(
    config: Mapping[str, Any], narrator_name: str, text: str | None = None
):
    return _compat_helper("_create_voicepeak_tuner")(config, narrator_name, text)


def voicepeak_profile_path(narrator_name: str) -> Path:
    return _compat_helper("_voicepeak_profile_path")(narrator_name)


def create_studio_engine(config: Mapping[str, Any], output_dir: Path | None = None):
    return _compat_helper("_create_studio_engine")(config, output_dir)


def create_studio_providers(config: Mapping[str, Any], provider_names: set[str]):
    return _compat_helper("_create_studio_providers")(config, provider_names)


def create_news_scheduler(
    config: Mapping[str, Any], provider: str | None = None, gemini_key: str | None = None,
):
    return _compat_helper("_create_news_scheduler")(config, provider, gemini_key)


def create_slack_components(config: Mapping[str, Any]):
    return _compat_helper("_create_slack_components")(config)
