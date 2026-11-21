"""対話式のProvider、資格情報、VOICEVOX音声設定。"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any

import typer
import yaml
from loguru import logger

from ...llm.defaults import ROUTE_OUTPUT_TOKENS_BY_PROVIDER
from ...secret_store import SecretStoreError, read_secret, write_kwallet_secret
from ...utils.atomic_io import atomic_replace_file
from . import composition
from .voicevox_parameter_menu import run_parameter_menu


def _choice(label: str, options: list[str], *, default: int = 1) -> int:
    typer.echo(f"\n{label}")
    for index, option in enumerate(options, start=1):
        typer.echo(f"  {index}. {option}")
    while True:
        selected = typer.prompt("番号", default=default, type=int)
        if 1 <= selected <= len(options):
            return selected - 1
        typer.echo(f"1〜{len(options)}を指定してください", err=True)


def _float_prompt(label: str, default: float, minimum: float, maximum: float) -> float:
    while True:
        value = typer.prompt(label, default=default, type=float)
        if minimum <= value <= maximum:
            return float(value)
        typer.echo(f"{minimum}〜{maximum}の範囲で指定してください", err=True)


def _parameter_prompts() -> dict[str, float]:
    """非TTY入力向けのVOICEVOXパラメーター設定。"""

    return {
        "speed_scale": _float_prompt("話速（0.5〜2.0）", 1.0, 0.5, 2.0),
        "pitch_scale": _float_prompt("ピッチ（-0.15〜0.15）", 0.0, -0.15, 0.15),
        "intonation_scale": _float_prompt("抑揚（0.0〜2.0）", 1.0, 0.0, 2.0),
        "volume_scale": _float_prompt("音量（0.0〜2.0）", 1.0, 0.0, 2.0),
    }


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _save_user_config(path: Path, overlay: dict[str, Any]) -> None:
    existing: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise typer.BadParameter(f"既存設定がmappingではありません: {path}")
        existing = loaded
    merged = _deep_merge(existing, overlay)

    def writer(temporary: Path) -> None:
        temporary.write_text(
            yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temporary.chmod(0o600)

    atomic_replace_file(path, writer)


def _opencode_secret_reference() -> dict[str, str]:
    if os.environ.get("OPENCODE_GO_API_KEY"):
        typer.echo("OPENCODE_GO_API_KEY環境変数を使用します（値は保存しません）。")
        return {"source": "environment", "variable": "OPENCODE_GO_API_KEY"}

    opencode_reference = {
        "source": "opencode_auth",
        "path": "~/.local/share/opencode/auth.json",
    }
    existing_key: str | None = None
    try:
        existing_key = read_secret(opencode_reference)
    except SecretStoreError:
        pass

    options: list[str] = []
    actions: list[str] = []
    if shutil.which("kwallet-query"):
        if existing_key:
            options.append("既存OpenCode資格情報をKWalletへ移行（推奨）")
            actions.append("import_kwallet")
        options.append("API keyを非表示入力してKWalletへ保存（推奨）")
        actions.append("prompt_kwallet")
    if existing_key:
        options.append("既存OpenCodeのmode 0600認証ファイルを参照")
        actions.append("opencode_auth")
    options.append("実行時にOPENCODE_GO_API_KEY環境変数を渡す")
    actions.append("environment")

    action = actions[_choice("OpenCode Go API keyの保管方法", options)]
    if action == "import_kwallet":
        assert existing_key is not None
        write_kwallet_secret(existing_key)
        existing_key = None
        typer.echo("API keyをKWalletへ保存しました（画面・YAMLには出力していません）。")
        return {"source": "kwallet", "wallet": "kdewallet", "folder": "humeina-unit"}
    if action == "prompt_kwallet":
        secret = typer.prompt("OpenCode Go API key", hide_input=True, confirmation_prompt=True)
        write_kwallet_secret(secret)
        secret = ""
        typer.echo("API keyをKWalletへ保存しました。")
        return {"source": "kwallet", "wallet": "kdewallet", "folder": "humeina-unit"}
    if action == "opencode_auth":
        return opencode_reference
    return {"source": "environment", "variable": "OPENCODE_GO_API_KEY"}


async def _voicevox_voices(url: str) -> list[dict[str, Any]]:
    return await composition.list_tts_voices("voicevox", {"url": url})


async def _preview_voicevox(settings: dict[str, Any], text: str) -> None:
    await composition.preview_tts_voice("voicevox", settings, text)


def _group_voicevox_voices(voices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """VOICEVOXの平坦なvoice一覧をキャラクター→スタイルへまとめる。"""

    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for voice in voices:
        display_name = str(voice.get("name", ""))
        character = str(voice.get("speaker_name") or "")
        style = str(voice.get("style_name") or "")
        # 古いProviderやpluginが返す「キャラクター（スタイル）」形式も受け付ける。
        if not character and display_name.endswith("）") and "（" in display_name:
            character, _, style_part = display_name.rpartition("（")
            style = style or style_part[:-1]
        character = character or display_name or f"話者ID {voice.get('id')}"
        style = style or display_name or f"スタイルID {voice.get('id')}"
        item = dict(voice)
        item["speaker_name"] = character
        item["style_name"] = style
        grouped.setdefault(character, []).append(item)
    return [
        {"name": character, "styles": styles}
        for character, styles in grouped.items()
    ]


def _configure_voicevox(*, preview: bool) -> dict[str, Any]:
    url = typer.prompt("VOICEVOX Engine URL", default="http://127.0.0.1:50021")
    voices = asyncio.run(_voicevox_voices(url))
    if not voices:
        raise typer.BadParameter(f"VOICEVOXの話者一覧を取得できません: {url}")
    characters = _group_voicevox_voices(voices)
    default_character = next(
        (
            index + 1
            for index, character in enumerate(characters)
            if any(int(style["id"]) == 47 for style in character["styles"])
        ),
        1,
    )
    character = characters[
        _choice(
            "VOICEVOXのキャラクター",
            [str(item["name"]) for item in characters],
            default=default_character,
        )
    ]
    styles = character["styles"]
    default_style = next(
        (index + 1 for index, style in enumerate(styles) if int(style["id"]) == 47),
        1,
    )
    selected = styles[
        _choice(
            f"{character['name']}のスタイル",
            [f"{style['style_name']}（ID: {style['id']}）" for style in styles],
            default=default_style,
        )
    ]
    base_settings = {
        "url": url,
        "default_speaker": int(selected["id"]),
    }
    initial_parameters = {
        "speed_scale": 1.0,
        "pitch_scale": 0.0,
        "intonation_scale": 1.0,
        "volume_scale": 1.0,
    }
    if sys.stdin.isatty() and sys.stdout.isatty():
        preview_text = ""
        if preview:
            preview_text = typer.prompt(
                "自動試聴テキスト",
                default="これはボイスボックスの音声設定テストです。",
            )

        async def live_preview(parameters: dict[str, float]) -> None:
            await _preview_voicevox(
                {**base_settings, **parameters},
                preview_text,
            )

        logger.disable("yomiage.tts")
        try:
            parameters = asyncio.run(
                run_parameter_menu(
                    initial_parameters,
                    preview=live_preview if preview else None,
                )
            )
        finally:
            logger.enable("yomiage.tts")
        if parameters is None:
            typer.echo("音声パラメーターの変更をキャンセルしました。")
            parameters = initial_parameters
    else:
        # CI、redirect、パイプ入力では矢印キーを利用できないため行入力を維持する。
        parameters = _parameter_prompts()

    settings = {**base_settings, **parameters}
    if (
        preview
        and not (sys.stdin.isatty() and sys.stdout.isatty())
        and typer.confirm("この設定で試聴しますか？", default=True)
    ):
        text = typer.prompt(
            "試聴テキスト",
            default="これはボイスボックスの音声設定テストです。",
        )
        asyncio.run(_preview_voicevox(settings, text))
    return settings


def configure(
    user_config: Path = typer.Option(
        Path("~/.config/humeina-unit/config.yaml"),
        "--user-config",
        help="保存するユーザーYAML",
    ),
    preview: bool = typer.Option(True, "--preview/--no-preview", help="VOICEVOXを試聴"),
) -> None:
    """LLM/TTS、API key、VOICEVOX音声を対話的に設定する。"""

    typer.echo("humeina-unit 対話セットアップ（秘密値はYAMLへ保存しません）")
    llm_index = _choice("LLMサービス", ["OpenCode Go（クラウド・既定）", "llama.cpp（ローカル）"])
    llm_provider = "opencode_go" if llm_index == 0 else "local"
    routes = {
        task: {"provider": llm_provider, "max_output_tokens": tokens}
        for task, tokens in ROUTE_OUTPUT_TOKENS_BY_PROVIDER[llm_provider].items()
    }
    overlay: dict[str, Any] = {
        "llm": {
            "providers": {
                "local": {"enabled": llm_provider == "local"},
                "opencode_go": {"enabled": llm_provider == "opencode_go"},
            },
            "routes": routes,
            "fallback": {"order": [llm_provider]},
        }
    }
    if llm_provider == "opencode_go":
        overlay["secrets"] = {"opencode_go": _opencode_secret_reference()}

    tts_index = _choice("TTSサービス", ["VOICEVOX（CPU・既定）", "VoiSona Talk", "VOICEPEAK"])
    tts_provider = ("voicevox", "voisona", "voicepeak")[tts_index]
    overlay["tts"] = {"primary_provider": tts_provider, "fallback_provider": None}
    overlay["batch"] = {"default_mode": tts_provider}
    if tts_provider == "voicevox":
        overlay["voicevox"] = _configure_voicevox(preview=preview)
    elif tts_provider == "voisona":
        overlay["voisona"] = {
            "url": typer.prompt("VoiSona Talk URL", default="http://127.0.0.1:32766")
        }
        typer.echo("認証情報はVOISONA_USERNAME/VOISONA_PASSWORD環境変数で渡してください。")
    else:
        overlay["voicepeak"] = {
            "path": typer.prompt("VOICEPEAK executableの絶対パス")
        }

    destination = user_config.expanduser()
    _save_user_config(destination, overlay)
    typer.echo(f"設定を保存しました: {destination} (mode 0600)")


def register(app: typer.Typer) -> None:
    app.command("configure")(configure)


__all__ = ["configure", "register"]
