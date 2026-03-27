"""Tests for studio synthesis cache."""

import struct
from pathlib import Path

from yomiage.studio.cache import SynthCache
from yomiage.studio.models import ScriptLine, SpeakerMapping


def _make_line(text: str = "テスト", speaker: str = "霊夢") -> ScriptLine:
    return ScriptLine(index=0, speaker=speaker, text=text, original_text=text)


def _make_mapping(
    speaker: str = "霊夢", provider: str = "voicevox", voice_id: str = "47"
) -> SpeakerMapping:
    return SpeakerMapping(speaker=speaker, provider=provider, voice_id=voice_id)


def _write_dummy_wav(path: Path) -> None:
    """最小限のWAVファイルを書き出す."""
    sample_rate = 24000
    num_samples = 100
    data_size = num_samples * 2
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))
        f.write(struct.pack("<H", 2))
        f.write(struct.pack("<H", 16))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(b"\x00" * data_size)


class TestCacheHitMiss:
    def test_miss_when_no_wav(self, tmp_path: Path):
        cache = SynthCache(tmp_path)
        line = _make_line()
        mapping = _make_mapping()
        wav = tmp_path / "test.wav"
        assert not cache.is_cached(line, mapping, wav)

    def test_hit_after_record(self, tmp_path: Path):
        cache = SynthCache(tmp_path)
        line = _make_line()
        mapping = _make_mapping()
        wav = tmp_path / "test.wav"
        _write_dummy_wav(wav)

        cache.record(line, mapping, wav)
        assert cache.is_cached(line, mapping, wav)

    def test_miss_on_text_change(self, tmp_path: Path):
        cache = SynthCache(tmp_path)
        line = _make_line("テスト1")
        mapping = _make_mapping()
        wav = tmp_path / "test.wav"
        _write_dummy_wav(wav)

        cache.record(line, mapping, wav)
        assert cache.is_cached(line, mapping, wav)

        # テキスト変更 → ミス
        line2 = _make_line("テスト2")
        assert not cache.is_cached(line2, mapping, wav)

    def test_miss_on_param_change(self, tmp_path: Path):
        cache = SynthCache(tmp_path)
        line = _make_line()
        mapping1 = SpeakerMapping(
            speaker="霊夢", provider="voicevox", voice_id="47",
            base_params={"speed": 1.0},
        )
        wav = tmp_path / "test.wav"
        _write_dummy_wav(wav)

        cache.record(line, mapping1, wav)
        assert cache.is_cached(line, mapping1, wav)

        # パラメータ変更 → ミス
        mapping2 = SpeakerMapping(
            speaker="霊夢", provider="voicevox", voice_id="47",
            base_params={"speed": 1.5},
        )
        assert not cache.is_cached(line, mapping2, wav)


class TestCachePersistence:
    def test_reload(self, tmp_path: Path):
        line = _make_line()
        mapping = _make_mapping()
        wav = tmp_path / "test.wav"
        _write_dummy_wav(wav)

        # 書き込み
        cache1 = SynthCache(tmp_path)
        cache1.record(line, mapping, wav)

        # 新しいインスタンスで読み込み
        cache2 = SynthCache(tmp_path)
        assert cache2.is_cached(line, mapping, wav)

    def test_cache_file_created(self, tmp_path: Path):
        cache = SynthCache(tmp_path)
        line = _make_line()
        mapping = _make_mapping()
        wav = tmp_path / "test.wav"
        _write_dummy_wav(wav)

        cache.record(line, mapping, wav)
        assert (tmp_path / "cache.json").exists()
