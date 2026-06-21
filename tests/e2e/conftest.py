"""E2Eテスト用の共有フィクスチャ.

実サービス（VOICEVOX / VoiSona Talk / VOICEPEAK / Ollama）が起動していない場合は
`pytest.skip` でテストをスキップする。これにより、CI 等でサービスが無くても
テストスイートが失敗しないようにする。
"""

from __future__ import annotations

import asyncio

import pytest
from dotenv import load_dotenv

from yomiage.config import get_tts_config, load_config
from yomiage.nlp.ollama_client import OllamaClient
from yomiage.tts.voicepeak import VoicepeakProvider
from yomiage.tts.voicevox import VoicevoxProvider
from yomiage.tts.voisona import VoisonaProvider


def _run(coro):
    """非同期コルーチンを同期的に実行するヘルパー."""
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def config():
    """プロジェクトの YAML 設定を読み込む（.env も反映）."""
    load_dotenv()
    return load_config()


def _service_available(config: dict, name: str) -> bool:
    """指定した外部サービスが利用可能か確認する."""
    if name == "voicevox":
        return _run(VoicevoxProvider(get_tts_config(config, "voicevox")).is_available())
    if name == "voisona":
        return _run(VoisonaProvider(get_tts_config(config, "voisona")).is_available())
    if name == "voicepeak":
        return _run(VoicepeakProvider(get_tts_config(config, "voicepeak")).is_available())
    if name == "ollama":
        cfg = config.get("ollama", {})
        return _run(
            OllamaClient(
                url=cfg.get("url", "http://localhost:11434"),
                model=cfg.get("model", "qwen3:8b"),
            ).is_available()
        )
    raise ValueError(f"Unknown service: {name}")


def skip_unless_service(config: dict, name: str) -> None:
    """サービスが利用できない場合はテストをスキップする."""
    if not _service_available(config, name):
        pytest.skip(f"{name} is not available")


@pytest.fixture
def require_voicevox(config):
    """VOICEVOX が利用可能な場合のみテストを実行する."""
    skip_unless_service(config, "voicevox")


@pytest.fixture
def require_voisona(config):
    """VoiSona Talk が利用可能な場合のみテストを実行する."""
    skip_unless_service(config, "voisona")


@pytest.fixture
def require_voicepeak(config):
    """VOICEPEAK が利用可能な場合のみテストを実行する."""
    skip_unless_service(config, "voicepeak")


@pytest.fixture
def require_ollama(config):
    """Ollama が利用可能な場合のみテストを実行する."""
    skip_unless_service(config, "ollama")
