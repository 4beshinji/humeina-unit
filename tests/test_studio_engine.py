"""Tests for studio engine — full pipeline with mock TTS provider."""

from __future__ import annotations

import asyncio
import json
import struct
import textwrap
from pathlib import Path

import pytest

from yomiage.studio.engine import StudioEngine
from yomiage.tts.base import AudioResult, TTSProvider


def _make_wav_bytes(duration: float = 1.0, sample_rate: int = 24000) -> bytes:
    """テスト用WAVバイト列を生成."""
    num_samples = int(sample_rate * duration)
    data_size = num_samples * 2
    buf = bytearray()
    buf.extend(b"RIFF")
    buf.extend(struct.pack("<I", 36 + data_size))
    buf.extend(b"WAVE")
    buf.extend(b"fmt ")
    buf.extend(struct.pack("<I", 16))
    buf.extend(struct.pack("<H", 1))
    buf.extend(struct.pack("<H", 1))
    buf.extend(struct.pack("<I", sample_rate))
    buf.extend(struct.pack("<I", sample_rate * 2))
    buf.extend(struct.pack("<H", 2))
    buf.extend(struct.pack("<H", 16))
    buf.extend(b"data")
    buf.extend(struct.pack("<I", data_size))
    buf.extend(b"\x00" * data_size)
    return bytes(buf)


class MockTTSProvider(TTSProvider):
    """テスト用モックTTSプロバイダー."""

    def __init__(self):
        self.synthesize_count = 0
        self._wav_data = _make_wav_bytes(1.0)

    @property
    def name(self) -> str:
        return "mock"

    async def synthesize(
        self, text: str, voice: str = "neutral", speed: float = 1.0, **params
    ) -> AudioResult:
        self.synthesize_count += 1
        return AudioResult(
            audio_data=self._wav_data,
            format="wav",
            sample_rate=24000,
            duration=1.0,
        )

    async def is_available(self) -> bool:
        return True

    async def list_voices(self) -> list[dict]:
        return [{"id": "1", "name": "mock_voice"}]


@pytest.fixture
def script_file(tmp_path: Path) -> Path:
    f = tmp_path / "script.txt"
    f.write_text(
        textwrap.dedent("""\
            霊夢: こんにちは、今日は量子コンピュータについて解説するわ
            魔理沙: 量子コンピュータって普通のパソコンと何が違うんだぜ？
            霊夢: 簡単に言うと計算の仕方が根本的に違うの
        """),
        encoding="utf-8",
    )
    return f


@pytest.fixture
def mock_provider() -> MockTTSProvider:
    return MockTTSProvider()


@pytest.fixture
def config() -> dict:
    return {
        "tts": {"primary_provider": "mock"},
        "voicevox": {"url": "http://localhost:50021", "default_speaker": 47},
        "studio": {
            "default_format": "ymm4",
            "default_pause": 0.3,
            "max_slug_chars": 15,
            "default_provider": "mock",
            "cache_enabled": True,
        },
    }


def _run(coro):
    """asyncio.run wrapper."""
    return asyncio.run(coro)


class TestFullPipeline:
    def test_synth_produces_output(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """フルパイプラインが正しい出力構造を生成する."""
        output_dir = tmp_path / "output"

        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        project = _run(engine.synth(
            script_path=script_file,
            provider="mock",
            output_format="ymm4",
        ))

        assert len(project.results) == 3
        assert mock_provider.synthesize_count == 3

        # 出力ディレクトリ構造
        project_dir = output_dir / script_file.stem
        assert (project_dir / "metadata.json").exists()
        assert (project_dir / "subtitles.srt").exists()
        assert (project_dir / "subtitles.ass").exists()
        assert (project_dir / "audio").is_dir()

    def test_ymm4_wav_txt_pairs(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """YMM4モードでは各WAVに対応する.txtファイルが生成される."""
        output_dir = tmp_path / "output"
        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        project = _run(engine.synth(
            script_path=script_file,
            provider="mock",
            output_format="ymm4",
        ))

        for r in project.results:
            assert r.wav_path.exists()
            assert r.txt_path is not None
            assert r.txt_path.exists()
            txt_content = r.txt_path.read_text(encoding="utf-8")
            assert len(txt_content) > 0

    def test_plain_format_no_txt(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """plainモードでは.txtファイルが生成されない."""
        output_dir = tmp_path / "output"
        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        project = _run(engine.synth(
            script_path=script_file,
            provider="mock",
            output_format="plain",
        ))

        for r in project.results:
            assert r.wav_path.exists()
            assert r.txt_path is None


class TestCaching:
    def test_cache_skip_on_second_run(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """2回目の合成ではキャッシュでスキップ."""
        output_dir = tmp_path / "output"
        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        _run(engine.synth(script_path=script_file, provider="mock"))
        assert mock_provider.synthesize_count == 3

        _run(engine.synth(script_path=script_file, provider="mock"))
        assert mock_provider.synthesize_count == 3  # 増えてない

    def test_no_cache_flag(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """no_cache=True で毎回合成."""
        output_dir = tmp_path / "output"
        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        _run(engine.synth(script_path=script_file, provider="mock"))
        _run(engine.synth(script_path=script_file, provider="mock", no_cache=True))
        assert mock_provider.synthesize_count == 6


class TestMetadata:
    def test_metadata_json_structure(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """metadata.json が正しい構造を持つ."""
        output_dir = tmp_path / "output"
        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        project = _run(engine.synth(script_path=script_file, provider="mock"))

        metadata_path = project.output_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        assert metadata["project"] == script_file.stem
        assert metadata["line_count"] == 3
        assert "霊夢" in metadata["speakers"]
        assert "魔理沙" in metadata["speakers"]
        assert len(metadata["lines"]) == 3
        assert metadata["total_duration"] > 0

        for entry in metadata["lines"]:
            assert "index" in entry
            assert "speaker" in entry
            assert "text" in entry
            assert "wav_file" in entry
            assert "duration" in entry


class TestSubtitles:
    def test_srt_timing(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """SRTのタイミングが正しく累計される."""
        output_dir = tmp_path / "output"
        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        project = _run(engine.synth(
            script_path=script_file, provider="mock", default_pause=0.5
        ))

        srt_path = project.output_dir / "subtitles.srt"
        content = srt_path.read_text(encoding="utf-8")
        assert "00:00:00,000" in content
        assert "00:00:01,500" in content

    def test_ass_generated(
        self, tmp_path: Path, script_file: Path, mock_provider: MockTTSProvider, config
    ):
        """ASSファイルが正しく生成される."""
        output_dir = tmp_path / "output"
        engine = StudioEngine(config, output_dir=output_dir)
        engine._create_providers = lambda m: {"mock": mock_provider}

        project = _run(engine.synth(script_path=script_file, provider="mock"))

        ass_path = project.output_dir / "subtitles.ass"
        content = ass_path.read_text(encoding="utf-8-sig")
        assert "[Script Info]" in content
        assert "[Events]" in content
        assert "Dialogue:" in content
