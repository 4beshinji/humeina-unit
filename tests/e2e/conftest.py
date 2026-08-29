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
from yomiage.llm.factory import create_llm_service
from yomiage.tts.voicepeak import VoicepeakProvider
from yomiage.tts.voicevox import VoicevoxProvider
from yomiage.tts.voisona import VoisonaProvider


def _run(coro):
    """非同期コルーチンを同期的に実行するヘルパー."""
    return asyncio.run(coro)


async def _available_and_close(service) -> bool:
    try:
        return await service.is_available()
    finally:
        close = getattr(service, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


@pytest.fixture(scope="session")
def config():
    """プロジェクトの YAML 設定を読み込む（.env も反映）."""
    load_dotenv()
    return load_config()


def _service_available(config: dict, name: str) -> bool:
    """指定した外部サービスが利用可能か確認する."""
    if name == "voicevox":
        return _run(
            _available_and_close(VoicevoxProvider(get_tts_config(config, "voicevox")))
        )
    if name == "voisona":
        return _run(
            _available_and_close(VoisonaProvider(get_tts_config(config, "voisona")))
        )
    if name == "voicepeak":
        return _run(
            _available_and_close(VoicepeakProvider(get_tts_config(config, "voicepeak")))
        )
    if name == "llm":
        return _run(_available_and_close(create_llm_service(config)))
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
def require_llm(config):
    """設定されたLLM Providerが利用可能な場合のみ実行する。"""
    skip_unless_service(config, "llm")
