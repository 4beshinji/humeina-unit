"""実スタック（本番サービス）を使った E2E テスト.

各テストは対象サービスが起動していない場合はスキップされる。
サービスが利用可能な環境では、以下を実際に検証する:

- TTS エンジン（VOICEVOX）を使った音声合成
- Pipeline API による「テキスト→分析→合成」の一連の流れ
- FastAPI サーバーの /api/yomiage/synthesize エンドポイント
- CLI の voices list コマンド
"""

from __future__ import annotations

import pytest

from yomiage.config import get_tts_config

pytestmark = [pytest.mark.e2e]


@pytest.mark.asyncio
async def test_voicevox_bridge_synthesis(config, require_voicevox, tmp_path):
    """VOICEVOX の実エンジンを使って TTSBridge から音声を合成する."""
    from yomiage.api.bridge import TTSBridge

    tts_cfg = get_tts_config(config, "voicevox")
    default_speaker = str(tts_cfg.get("default_speaker", 47))

    bridge = TTSBridge.create("voicevox", **tts_cfg)
    result = await bridge.synthesize(
        "これは実スタックの音声合成テストです。",
        voice_id=default_speaker,
    )

    assert result.format == "wav"
    assert len(result.audio_data) > 0

    out_path = tmp_path / "e2e_voicevox.wav"
    result.save(out_path)
    assert out_path.exists()
    assert out_path.stat().st_size > 0


@pytest.mark.asyncio
async def test_pipeline_with_voicevox(config, require_voicevox):
    """Pipeline API で実際の VOICEVOX エンジンを使い、文を分析・合成する."""
    from yomiage.api.pipeline import Pipeline

    tts_cfg = get_tts_config(config, "voicevox")
    pipeline = Pipeline.create(
        "voicevox",
        url=tts_cfg.get("url"),
        default_voice=str(tts_cfg.get("default_speaker", 47)),
    )

    chunks = await pipeline.process(
        "太郎は「こんにちは」と言った。花子は笑った。"
    )
    assert chunks

    valid_types = {"dialogue", "narration", "thought"}
    for chunk in chunks:
        assert chunk.text
        assert chunk.audio.format == "wav"
        assert len(chunk.audio.audio_data) > 0
        assert chunk.analysis.segment_type in valid_types


def test_server_synthesize_voicevox(config, require_voicevox, monkeypatch):
    """FastAPI サーバーの synthesize エンドポイントを実 VOICEVOX で検証する.

    設定ファイルの primary_provider が voisona になっている可能性があるため、
    テスト中のみサーバーのエンジン作成関数を VOICEVOX 固定に差し替える。
    """
    from fastapi.testclient import TestClient

    from yomiage.cli import _create_reading_engine
    from yomiage.server import app

    def _engine_with_voicevox(cfg: dict):
        return _create_reading_engine(cfg, provider_override="voicevox")

    monkeypatch.setattr("yomiage.server._create_engine", _engine_with_voicevox)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        response = client.post(
            "/api/yomiage/synthesize",
            json={
                "text": "実スタックのサーバーテストです。",
                "speed": 1.0,
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/wav")
        audio_data = response.content
        assert len(audio_data) > 0
        assert audio_data.startswith(b"RIFF")


def test_cli_voices_list_voicevox(config, require_voicevox):
    """CLI の voices list コマンドが実 VOICEVOX のボイス一覧を返す."""
    from typer.testing import CliRunner

    from yomiage.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["voices", "list", "--provider", "voicevox"])

    assert result.exit_code == 0
    assert "(unavailable)" not in result.output
    assert "voicevox" in result.output.lower() or "47" in result.output


@pytest.mark.asyncio
async def test_voisona_provider_synthesis(config, require_voisona):
    """VoiSona Talk の実エンジンを使って音声合成を実行する.

    VoiSona Talk は VM 側のスピーカーで再生するため、audio_data は空で
    duration が返ることを検証する。
    """
    from yomiage.tts.voisona import VoisonaProvider

    tts_cfg = get_tts_config(config, "voisona")
    provider = VoisonaProvider(tts_cfg)
    result = await provider.synthesize(
        "これはVoiSona Talkの実スタックテストです。",
        voice="neutral",
        speed=1.0,
    )

    assert result.format == "wav"
    assert result.duration and result.duration > 0


@pytest.mark.asyncio
async def test_voisona_list_voices(config, require_voisona):
    """VoiSona Talk の実エンジンからボイス一覧を取得する."""
    from yomiage.tts.voisona import VoisonaProvider

    voices = await VoisonaProvider(get_tts_config(config, "voisona")).list_voices()
    assert voices
    assert any("id" in v and "name" in v for v in voices)
