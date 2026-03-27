"""Tests for studio script parser."""

import json
import textwrap
from pathlib import Path

import pytest

from yomiage.studio.script_parser import ScriptParser


@pytest.fixture
def parser():
    return ScriptParser(max_chars=200)


class TestPlainTextParsing:
    def test_basic_speaker_lines(self, parser: ScriptParser):
        content = textwrap.dedent("""\
            霊夢: こんにちは、今日は量子コンピュータについて解説するわ
            魔理沙: 量子コンピュータって普通のパソコンと何が違うんだぜ？
        """)
        lines = parser.parse_text(content)
        assert len(lines) == 2
        assert lines[0].speaker == "霊夢"
        assert lines[0].text == "こんにちは、今日は量子コンピュータについて解説するわ"
        assert lines[1].speaker == "魔理沙"
        assert lines[1].index == 1

    def test_fullwidth_colon(self, parser: ScriptParser):
        content = "霊夢：全角コロンも対応"
        lines = parser.parse_text(content)
        assert len(lines) == 1
        assert lines[0].speaker == "霊夢"
        assert lines[0].text == "全角コロンも対応"

    def test_comment_lines_ignored(self, parser: ScriptParser):
        content = textwrap.dedent("""\
            # これはコメント
            霊夢: セリフです
            # もう一つのコメント
        """)
        lines = parser.parse_text(content)
        assert len(lines) == 1
        assert lines[0].text == "セリフです"

    def test_empty_lines_as_pause(self, parser: ScriptParser):
        content = textwrap.dedent("""\
            霊夢: 最初のセリフ

            魔理沙: 次のセリフ
        """)
        lines = parser.parse_text(content)
        assert len(lines) == 2
        assert lines[0].pause_after == -1.0  # sentinel for default pause

    def test_pause_marker(self, parser: ScriptParser):
        content = textwrap.dedent("""\
            霊夢: セリフ
            （間）
            魔理沙: 次
        """)
        lines = parser.parse_text(content)
        assert len(lines) == 2
        assert lines[0].pause_after == -1.0

    def test_pause_marker_variants(self, parser: ScriptParser):
        for marker in ["（間）", "(ポーズ)", "（pause）", "(間)"]:
            content = f"霊夢: テスト\n{marker}\n魔理沙: 次"
            lines = parser.parse_text(content)
            assert lines[0].pause_after == -1.0, f"Failed for marker: {marker}"

    def test_continuation_lines(self, parser: ScriptParser):
        content = textwrap.dedent("""\
            霊夢: 最初の行
            続きの行
        """)
        lines = parser.parse_text(content)
        assert len(lines) == 2
        assert lines[0].speaker == "霊夢"
        assert lines[1].speaker == "霊夢"
        assert lines[1].text == "続きの行"

    def test_long_line_split(self):
        parser = ScriptParser(max_chars=20)
        long_text = "これはテストです。とても長い文章です。分割されるはずです。"
        content = f"霊夢: {long_text}"
        lines = parser.parse_text(content)
        assert len(lines) > 1
        for line in lines:
            assert line.speaker == "霊夢"

    def test_empty_content(self, parser: ScriptParser):
        lines = parser.parse_text("")
        assert lines == []

    def test_only_comments(self, parser: ScriptParser):
        content = "# comment 1\n# comment 2"
        lines = parser.parse_text(content)
        assert lines == []


class TestCSVParsing:
    def test_basic_csv(self, parser: ScriptParser, tmp_path: Path):
        csv_file = tmp_path / "script.csv"
        csv_file.write_text(
            "speaker,text,emotion\n"
            "霊夢,こんにちは,happy\n"
            "魔理沙,よう,neutral\n",
            encoding="utf-8",
        )
        lines = parser.parse_csv(csv_file)
        assert len(lines) == 2
        assert lines[0].speaker == "霊夢"
        assert lines[0].emotion == "happy"
        assert lines[1].emotion == "neutral"

    def test_csv_without_emotion(self, parser: ScriptParser, tmp_path: Path):
        csv_file = tmp_path / "script.csv"
        csv_file.write_text(
            "speaker,text\n"
            "霊夢,こんにちは\n",
            encoding="utf-8",
        )
        lines = parser.parse_csv(csv_file)
        assert len(lines) == 1
        assert lines[0].emotion == "neutral"

    def test_csv_empty_rows_skipped(self, parser: ScriptParser, tmp_path: Path):
        csv_file = tmp_path / "script.csv"
        csv_file.write_text(
            "speaker,text\n"
            ",\n"
            "霊夢,テスト\n",
            encoding="utf-8",
        )
        lines = parser.parse_csv(csv_file)
        assert len(lines) == 1


class TestJSONParsing:
    def test_basic_json(self, parser: ScriptParser, tmp_path: Path):
        json_file = tmp_path / "script.json"
        data = [
            {"speaker": "霊夢", "text": "こんにちは", "emotion": "happy"},
            {"speaker": "魔理沙", "text": "よう"},
        ]
        json_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        lines = parser.parse_json(json_file)
        assert len(lines) == 2
        assert lines[0].emotion == "happy"
        assert lines[1].emotion == "neutral"


class TestAutoDetection:
    def test_detect_csv(self, parser: ScriptParser, tmp_path: Path):
        f = tmp_path / "script.csv"
        f.write_text("speaker,text\n霊夢,テスト\n", encoding="utf-8")
        lines = parser.parse(f)
        assert len(lines) == 1

    def test_detect_json(self, parser: ScriptParser, tmp_path: Path):
        f = tmp_path / "script.json"
        f.write_text(
            json.dumps([{"speaker": "霊夢", "text": "テスト"}], ensure_ascii=False),
            encoding="utf-8",
        )
        lines = parser.parse(f)
        assert len(lines) == 1

    def test_detect_txt(self, parser: ScriptParser, tmp_path: Path):
        f = tmp_path / "script.txt"
        f.write_text("霊夢: テスト\n", encoding="utf-8")
        lines = parser.parse(f)
        assert len(lines) == 1

    def test_indexes_sequential(self, parser: ScriptParser):
        content = "霊夢: A\n魔理沙: B\n霊夢: C\n"
        lines = parser.parse_text(content)
        for i, line in enumerate(lines):
            assert line.index == i
