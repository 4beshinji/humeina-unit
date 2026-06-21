"""Tests for TTSBridge."""

from unittest.mock import AsyncMock, patch

import pytest

from yomiage.api.bridge import TTSBridge, _build_synth_kwargs, _to_synthesis_result
from yomiage.api.config import TTSEngineConfig
from yomiage.api.models import SynthesisResult, VoiceInfo
from yomiage.tts.base import AudioResult, TTSParams, TTSProvider


def _make_wav_bytes(duration: float = 0.1, sample_rate: int = 24000) -> bytes:
    """有効な無音 WAV バイト列を生成."""
    import io
    import wave

    num_frames = int(sample_rate * duration)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * (num_frames * 2))
    return buf.getvalue()


class MockProvider(TTSProvider):
    """テスト用モックプロバイダー."""

    def __init__(self, name: str = "mock", audio_data: bytes = b"RIFF_mock_wav"):
        self._name = name
        self._audio_data = audio_data
        self.synthesize_calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    async def synthesize(self, text, voice="neutral", speed=1.0, **params):
        self.synthesize_calls.append(
            {"text": text, "voice": voice, "speed": speed, **params}
        )
        return AudioResult(
            audio_data=self._audio_data, format="wav", sample_rate=24000, duration=1.0
        )

    async def is_available(self) -> bool:
        return True

    async def list_voices(self) -> list[dict]:
        return [
            {"id": "1", "label": "Voice A", "gender": "female", "age_group": "adult"},
            {"id": "2", "label": "Voice B", "gender": "male"},
        ]


class MockEmptyAudioProvider(MockProvider):
    """audio_dataが空のプロバイダー（VoiSona模擬）."""

    def __init__(self):
        super().__init__(name="voisona-mock", audio_data=b"")
        self.file_synthesize_calls: list[dict] = []

    async def synthesize_to_file(self, text, output_path, voice="neutral", speed=1.0, **params):
        self.file_synthesize_calls.append(
            {"text": text, "output_path": output_path}
        )
        # ファイルにダミーデータ書き出し
        from pathlib import Path
        Path(output_path).write_bytes(b"RIFF_file_wav")
        return AudioResult(audio_data=b"", format="wav", sample_rate=44100, duration=2.0)


class TestTTSBridgeInit:
    def test_from_provider(self):
        provider = MockProvider()
        bridge = TTSBridge.from_provider(provider)
        assert bridge.engine_name == "mock"

    def test_from_config(self):
        with patch("yomiage.api.bridge.create_provider") as mock_create:
            mock_create.return_value = MockProvider()
            config = TTSEngineConfig(engine="voicevox")
            bridge = TTSBridge.from_config(config)
            assert bridge.engine_name == "mock"


class TestTTSBridgeSynthesize:
    @pytest.mark.asyncio
    async def test_basic_synthesize(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        result = await bridge.synthesize("テスト")
        assert isinstance(result, SynthesisResult)
        assert result.audio_data == b"RIFF_mock_wav"
        assert result.format == "wav"
        assert result.sample_rate == 24000
        assert result.duration == 1.0

    @pytest.mark.asyncio
    async def test_synthesize_with_params(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        await bridge.synthesize("テスト", voice_id="47", speed=1.2, pitch=100.0)
        call = provider.synthesize_calls[-1]
        assert call["speed"] == 1.2
        assert call["voice_id"] == "47"
        assert call["pitch"] == 100.0

    @pytest.mark.asyncio
    async def test_synthesize_with_tts_params(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        params = TTSParams(voice_id="99", speed=0.8, pitch=-50.0)
        await bridge.synthesize("テスト", params=params)
        call = provider.synthesize_calls[-1]
        assert call["speed"] == 0.8
        assert call["voice_id"] == "99"
        assert call["pitch"] == -50.0

    @pytest.mark.asyncio
    async def test_synthesize_empty_audio_uses_file(self, tmp_path):
        """VoiSona模擬: audio_data空の場合はファイル経由."""
        provider = MockEmptyAudioProvider()
        bridge = TTSBridge(provider)
        result = await bridge.synthesize("テスト")
        assert result.audio_data == b"RIFF_file_wav"
        assert len(provider.file_synthesize_calls) == 1

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self):
        primary = MockProvider(name="primary")
        primary.synthesize = AsyncMock(side_effect=RuntimeError("down"))
        fallback = MockProvider(name="fallback", audio_data=b"FALLBACK")
        bridge = TTSBridge(primary, fallback)
        result = await bridge.synthesize("テスト")
        assert result.audio_data == b"FALLBACK"

    @pytest.mark.asyncio
    async def test_no_fallback_raises(self):
        primary = MockProvider()
        primary.synthesize = AsyncMock(side_effect=RuntimeError("down"))
        bridge = TTSBridge(primary)
        with pytest.raises(RuntimeError, match="down"):
            await bridge.synthesize("テスト")


class TestTTSBridgeSynthesizeToFile:
    @pytest.mark.asyncio
    async def test_synthesize_to_file(self, tmp_path):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        output = tmp_path / "out.wav"
        result = await bridge.synthesize_to_file("テスト", output)
        assert isinstance(result, SynthesisResult)
        assert result.audio_data == b"RIFF_mock_wav"


class TestTTSBridgeListVoices:
    @pytest.mark.asyncio
    async def test_list_voices(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        voices = await bridge.list_voices()
        assert len(voices) == 2
        assert all(isinstance(v, VoiceInfo) for v in voices)
        assert voices[0].id == "1"
        assert voices[0].name == "Voice A"
        assert voices[0].engine == "mock"
        assert voices[0].gender == "female"

    @pytest.mark.asyncio
    async def test_is_available(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        assert await bridge.is_available() is True


class TestTTSBridgeLongText:
    @pytest.mark.asyncio
    async def test_synthesize_long_short_text(self):
        provider = MockProvider(audio_data=_make_wav_bytes())
        bridge = TTSBridge(provider)
        result = await bridge.synthesize_long("テスト文章です。", max_chars=200)
        assert isinstance(result, SynthesisResult)
        assert len(result.audio_data) > 0

    @pytest.mark.asyncio
    async def test_synthesize_long_splits_text(self):
        provider = MockProvider(audio_data=_make_wav_bytes())
        bridge = TTSBridge(provider)
        text = "これはテストです。" * 20
        result = await bridge.synthesize_long(text, max_chars=20)
        assert isinstance(result, SynthesisResult)
        # concat_wav_bytes で結合されたため、複数回合成されたはず
        assert len(provider.synthesize_calls) > 1

    @pytest.mark.asyncio
    async def test_synthesize_long_empty(self):
        provider = MockProvider(audio_data=_make_wav_bytes())
        bridge = TTSBridge(provider)
        result = await bridge.synthesize_long("")
        assert result.audio_data == b""


class TestTTSBridgeSync:
    def test_synthesize_sync(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        result = bridge.synthesize_sync("テスト")
        assert isinstance(result, SynthesisResult)
        assert result.audio_data == b"RIFF_mock_wav"

    def test_list_voices_sync(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        voices = bridge.list_voices_sync()
        assert len(voices) == 2

    def test_is_available_sync(self):
        provider = MockProvider()
        bridge = TTSBridge(provider)
        assert bridge.is_available_sync() is True


class TestSynthesisResult:
    def test_save(self, tmp_path):
        result = SynthesisResult(audio_data=b"wav_data", format="wav")
        path = result.save(tmp_path / "subdir" / "test.wav")
        assert path.exists()
        assert path.read_bytes() == b"wav_data"


def _has_ffmpeg() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


class TestTTSBridgeVoiceProfile:
    @pytest.mark.asyncio
    async def test_synthesize_with_voice_profile(self):
        from yomiage.tools.voice_profile import VoiceProfile

        profile = VoiceProfile.create_default(
            "mock_voice", "Mock Voice", style_names=[]
        )
        provider = MockProvider(audio_data=_make_wav_bytes())
        bridge = TTSBridge(provider, voice_profile=profile)

        await bridge.synthesize("テスト", preset="female_young", emotion="happy")
        call = provider.synthesize_calls[-1]
        # VoiceProfile の計算結果が反映されている
        assert "pitch" in call or "speed" in call

    @pytest.mark.asyncio
    async def test_explicit_args_override_profile(self):
        from yomiage.tools.voice_profile import VoiceProfile

        profile = VoiceProfile.create_default(
            "mock_voice", "Mock Voice", style_names=[]
        )
        provider = MockProvider(audio_data=_make_wav_bytes())
        bridge = TTSBridge(provider, voice_profile=profile)

        await bridge.synthesize(
            "テスト", preset="female_young", speed=1.5, pitch=100.0
        )
        call = provider.synthesize_calls[-1]
        assert call["speed"] == 1.5
        assert call["pitch"] == 100.0


class TestTTSBridgeCache:
    @pytest.mark.asyncio
    async def test_cache_returns_cached_result(self, tmp_path):
        from yomiage.tts.cache import TTSCache

        cache = TTSCache(cache_dir=tmp_path / "cache")
        provider = MockProvider(audio_data=_make_wav_bytes())
        bridge = TTSBridge(provider, cache=cache)

        result1 = await bridge.synthesize("キャッシュテスト")
        assert len(result1.audio_data) > 0

        provider.synthesize_calls.clear()
        result2 = await bridge.synthesize("キャッシュテスト")
        assert result2.audio_data == result1.audio_data
        assert len(provider.synthesize_calls) == 0

    @pytest.mark.asyncio
    async def test_cache_disabled(self, tmp_path):
        from yomiage.tts.cache import TTSCache

        cache = TTSCache(cache_dir=tmp_path / "cache", enabled=False)
        provider = MockProvider(audio_data=_make_wav_bytes())
        bridge = TTSBridge(provider, cache=cache)

        await bridge.synthesize("キャッシュテスト")
        provider.synthesize_calls.clear()
        await bridge.synthesize("キャッシュテスト")
        assert len(provider.synthesize_calls) == 1


class TestSynthesisResultConvert:
    def test_convert_wav_returns_same(self):
        result = SynthesisResult(audio_data=b"wav_data", format="wav")
        converted = result.convert("wav")
        assert converted.audio_data == b"wav_data"
        assert converted.format == "wav"

    @pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")
    def test_convert_to_mp3(self):
        wav = _make_wav_bytes()
        result = SynthesisResult(audio_data=wav, format="wav", sample_rate=24000)
        converted = result.convert("mp3")
        assert converted.format == "mp3"
        assert len(converted.audio_data) > 0

    def test_convert_unsupported_format(self):
        result = SynthesisResult(audio_data=b"wav_data", format="wav")
        from yomiage.api.exceptions import ValidationError

        with pytest.raises(ValidationError):
            result.convert("unknown")


class TestBuildSynthKwargs:
    def test_defaults(self):
        kwargs = _build_synth_kwargs()
        assert kwargs == {"speed": 1.0}

    def test_with_voice_id(self):
        kwargs = _build_synth_kwargs(voice_id="47")
        assert kwargs["voice_id"] == "47"

    def test_with_tts_params_override(self):
        params = TTSParams(voice_id="99", speed=0.5, huskiness=5.0)
        kwargs = _build_synth_kwargs(params=params)
        assert kwargs["voice_id"] == "99"
        assert kwargs["speed"] == 0.5
        assert kwargs["huskiness"] == 5.0


class TestToSynthesisResult:
    def test_conversion(self):
        audio = AudioResult(audio_data=b"data", format="wav", sample_rate=44100, duration=3.0)
        result = _to_synthesis_result(audio)
        assert isinstance(result, SynthesisResult)
        assert result.audio_data == b"data"
        assert result.sample_rate == 44100
