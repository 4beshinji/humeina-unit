"""Tests for studio file naming."""

from yomiage.studio.models import ScriptLine
from yomiage.studio.naming import FileNamer


def _make_line(index: int = 0, speaker: str = "霊夢", text: str = "テスト") -> ScriptLine:
    return ScriptLine(
        index=index, speaker=speaker, text=text, original_text=text
    )


class TestYMM4Naming:
    def test_wav_name(self):
        namer = FileNamer(format="ymm4")
        line = _make_line(0, "霊夢", "こんにちは今日は量子コンピュータについて解説するわ")
        name = namer.wav_name(line)
        assert name.startswith("001_霊夢_")
        assert name.endswith(".wav")

    def test_txt_name(self):
        namer = FileNamer(format="ymm4")
        line = _make_line(0, "霊夢", "テスト")
        txt = namer.txt_name(line)
        assert txt is not None
        assert txt.endswith(".txt")
        # wav and txt should share the same stem
        wav = namer.wav_name(line)
        assert wav.replace(".wav", "") == txt.replace(".txt", "")

    def test_slug_truncation(self):
        namer = FileNamer(format="ymm4", max_slug_chars=5)
        line = _make_line(0, "霊夢", "あいうえおかきくけこ")
        name = namer.wav_name(line)
        # slug should be 5 chars max
        assert "あいうえお" in name
        assert "かきくけこ" not in name

    def test_slug_fs_safe(self):
        namer = FileNamer(format="ymm4")
        line = _make_line(0, "霊夢", 'テスト/パス\\NG:?*"<>|文字')
        name = namer.wav_name(line)
        for ch in '/\\:*?"<>|':
            assert ch not in name


class TestPlainNaming:
    def test_wav_name(self):
        namer = FileNamer(format="plain")
        line = _make_line(0, "霊夢", "テスト")
        assert namer.wav_name(line) == "001.wav"

    def test_txt_name_none(self):
        namer = FileNamer(format="plain")
        line = _make_line(0, "霊夢", "テスト")
        assert namer.txt_name(line) is None


class TestSequentialNumbering:
    def test_numbering(self):
        namer = FileNamer(format="plain")
        for i in range(10):
            line = _make_line(i, "霊夢", f"テスト{i}")
            name = namer.wav_name(line)
            assert name == f"{i + 1:03d}.wav"

    def test_no_collision(self):
        namer = FileNamer(format="ymm4")
        lines = [_make_line(i, "霊夢", "同じテキスト") for i in range(3)]
        names = [namer.wav_name(line) for line in lines]
        assert len(set(names)) == 3  # all unique due to index prefix
