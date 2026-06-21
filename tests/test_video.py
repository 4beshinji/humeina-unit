"""Tests for video generation module (Phase D).

Test content derived from ../hems character profiles:
- ナースロボ＿タイプT: 慎み深いナース型アンドロイド
- エナ: ハイテンションデジタル居候
"""

import io
import json
import struct
import wave
from pathlib import Path

import pytest

from yomiage.batch.manifest import BatchManifest, ChapterMeta, SentenceEntry
from yomiage.tts.audio_utils import get_wav_duration
from yomiage.video.asset_manager import AssetManager
from yomiage.video.config import BackgroundConfig, SubtitleConfig, VideoConfig
from yomiage.video.subtitle import (
    SubtitleGenerator,
    _format_ass_time,
    _format_srt_time,
    _hex_to_ass_color,
    _wrap_text,
)
from yomiage.video.timeline import (
    TimelineBuilder,
    TimelineEvent,
)

# ============================================================
# Test data from hems character profiles
# ============================================================

# ナースロボ＿タイプT — 慎み深いナース語録
_NURSEROBO_LINES: list[tuple[str, str, str | None, str, str]] = [
    ("お身体、大丈夫ですか？", "narration", None, "daily", "neutral"),
    (
        "換気してください。CO2が高めです。",
        "dialogue", "ナースロボ", "tense", "angry",
    ),
    (
        "お部屋が少し暑いですね。エアコン、つけましょうか。",
        "dialogue", "ナースロボ", "daily", "neutral",
    ),
    (
        "まだ起きていますか？ 早めに休んでくださいね。",
        "dialogue", "ナースロボ", "daily", "sad",
    ),
    (
        "少し歩きませんか。ずっと座っていると、よくないですから。",
        "dialogue", "ナースロボ", "daily", "neutral",
    ),
    (
        "あなた、心拍が高いです。大丈夫ですか？",
        "dialogue", "ナースロボ", "tense", "angry",
    ),
    ("ありがとうございます。", "dialogue", "ナースロボ", "daily", "happy"),
    ("お疲れさまでした。", "narration", None, "daily", "neutral"),
    (
        "申し訳ありません、それはできません。あなたのためです。",
        "dialogue", "ナースロボ", "sad", "sad",
    ),
]

# エナ — ハイテンションデジタル居候語録
_ENA_LINES: list[tuple[str, str, str | None, str, str]] = [
    (
        "ねぇ、ご主人！最強のデジタルアシスタント・エナちゃんですよ！",
        "dialogue", "エナ", "comedy", "happy",
    ),
    (
        "CPU使用率がご主人の労働意欲より高いですよ！",
        "dialogue", "エナ", "comedy", "happy",
    ),
    ("ちょっと！大丈夫ですか！？", "dialogue", "エナ", "tense", "angry"),
    ("おやすみなさい、ご主人。", "dialogue", "エナ", "daily", "sad"),
    ("当然ですよ！わたしですから！", "dialogue", "エナ", "comedy", "happy"),
    (
        "わたし、データの海を泳ぐの得意なんですよ？",
        "dialogue", "エナ", "comedy", "neutral",
    ),
    ("わたしの家が熱いんですけど！", "dialogue", "エナ", "battle", "angry"),
    (
        "ご主人の秘密のメモ、見ちゃいましたよ。",
        "dialogue", "エナ", "comedy", "happy",
    ),
]

# タスク完了メッセージ（CONTEXT_AWARE_COMPLETION.md より）
_COMPLETION_LINES: list[tuple[str, str, str | None, str, str]] = [
    (
        "ありがとうございます！これで皆が気持ちよく過ごせますね。",
        "narration", None, "daily", "happy",
    ),
    (
        "ありがとうございます！これで美味しいコーヒーが飲めますね。",
        "narration", None, "daily", "happy",
    ),
    (
        "ありがとうございます！これで作業がスムーズに進みます。",
        "narration", None, "daily", "happy",
    ),
]


def _make_wav_bytes(duration: float = 1.0, sample_rate: int = 24000) -> bytes:
    """テスト用のサイレントWAVデータを生成."""
    n_frames = int(sample_rate * duration)
    data = struct.pack(f"<{n_frames}h", *([0] * n_frames))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)
    return buf.getvalue()


def _create_test_manifest(
    work_dir: Path,
    lines: list[tuple[str, str, str | None, str, str]] | None = None,
    create_wavs: bool = True,
    wav_duration: float = 1.5,
) -> BatchManifest:
    """テスト用マニフェストを生成.

    hems character dataを使った2チャプター構成:
      Chapter 1: ナースロボの看護記録（日常 → 緊張シーン）
      Chapter 2: エナの居候ライフ（コメディ → バトル）
    """
    if lines is None:
        lines = (
            _NURSEROBO_LINES
            + [("", "scene_break", None, "daily", "neutral")]
            + _ENA_LINES
        )

    sentences: list[SentenceEntry] = []
    ch1_end = len(_NURSEROBO_LINES) + 1  # +1 for scene_break

    for i, (text, seg_type, speaker, scene, emotion) in enumerate(lines):
        chapter_index = 0 if i < ch1_end else 1
        audio_file = (
            f"{i:04d}.wav"
            if seg_type != "scene_break" or create_wavs
            else None
        )

        entry = SentenceEntry(
            index=i,
            text=text,
            chapter_index=chapter_index,
            segment_type=seg_type,
            speaker=speaker,
            scene=scene,
            emotion=emotion,
            intensity=0.7 if emotion != "neutral" else 0.3,
            audio_file=audio_file,
            status="synthesized",
        )
        sentences.append(entry)

        if create_wavs and audio_file:
            dur = 0.5 if seg_type == "scene_break" else wav_duration
            wav_data = _make_wav_bytes(duration=dur)
            (work_dir / audio_file).write_bytes(wav_data)

    manifest = BatchManifest(
        work_id="test_hems_chars",
        work_title="HEMS AIキャラクター会話集",
        source_url="file:///hems/config/characters/",
        mode="voisona",
        chapters=[
            ChapterMeta(
                index=0,
                title="ナースロボの看護記録",
                url="file:///hems/nurserobo-typet.yaml",
                sentence_start=0,
                sentence_end=ch1_end,
            ),
            ChapterMeta(
                index=1,
                title="エナの居候ライフ",
                url="file:///hems/ena.yaml",
                sentence_start=ch1_end,
                sentence_end=len(lines),
            ),
        ],
        characters={
            "ナースロボ": {
                "name": "ナースロボ",
                "gender": "female",
                "age_group": "young_adult",
                "personality": "慎み深いナース型アンドロイド",
            },
            "エナ": {
                "name": "エナ",
                "gender": "female",
                "age_group": "teen",
                "personality": "ハイテンションデジタル居候",
            },
        },
        sentences=sentences,
        analysis_complete=True,
        synthesis_complete=True,
    )
    return manifest


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    """テスト用作業ディレクトリ."""
    d = tmp_path / "output" / "test_hems_chars"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def manifest_with_wavs(work_dir: Path) -> BatchManifest:
    """WAVファイル付きのテスト用マニフェスト."""
    return _create_test_manifest(work_dir)


@pytest.fixture
def manifest_no_wavs(work_dir: Path) -> BatchManifest:
    """WAVファイル無しのテスト用マニフェスト."""
    return _create_test_manifest(work_dir, create_wavs=False)


@pytest.fixture
def video_config() -> VideoConfig:
    """テスト用VideoConfig（低解像度で高速化）."""
    return VideoConfig(
        resolution=(640, 360),
        fps=10,
        crf=30,
        preset="ultrafast",
        subtitle=SubtitleConfig(
            font_size=24,
            font_name="sans-serif",
            outline_size=2,
            margin_bottom=30,
            max_chars_per_line=15,
            speaker_colors={
                "_narrator": "#FFFFFF",
                "_dialogue": "#FFFF00",
                "_thought": "#87CEEB",
                "ナースロボ": "#98D8C8",
                "エナ": "#FF6B6B",
            },
        ),
        background=BackgroundConfig(
            scene_colors={
                "daily": "#2C3E50",
                "battle": "#8B0000",
                "romance": "#FF69B4",
                "tense": "#1C1C1C",
                "comedy": "#FFD700",
                "sad": "#4A4A8A",
                "horror": "#0D0D0D",
            },
        ),
    )


# ============================================================
# VideoConfig tests
# ============================================================


class TestVideoConfig:
    def test_default(self):
        cfg = VideoConfig()
        assert cfg.resolution == (1920, 1080)
        assert cfg.fps == 24
        assert cfg.codec == "libx264"
        assert cfg.crf == 23
        assert cfg.subtitle.font_size == 48
        assert "_narrator" in cfg.subtitle.speaker_colors

    def test_from_dict_empty(self):
        cfg = VideoConfig.from_dict({})
        assert cfg.resolution == (1920, 1080)

    def test_from_dict_partial(self):
        cfg = VideoConfig.from_dict({
            "resolution": [1280, 720],
            "fps": 30,
            "subtitle": {"font_size": 36},
        })
        assert cfg.resolution == (1280, 720)
        assert cfg.fps == 30
        assert cfg.subtitle.font_size == 36
        assert cfg.codec == "libx264"
        assert cfg.subtitle.outline_size == 3

    def test_from_dict_with_scene_colors(self):
        cfg = VideoConfig.from_dict({
            "background": {
                "scene_colors": {
                    "daily": "#000000",
                    "custom": "#FF0000",
                },
            },
        })
        assert cfg.background.scene_colors["daily"] == "#000000"
        assert cfg.background.scene_colors["custom"] == "#FF0000"

    def test_from_dict_none(self):
        cfg = VideoConfig.from_dict(None)
        assert cfg.resolution == (1920, 1080)


# ============================================================
# WAV duration tests
# ============================================================


class TestWavDuration:
    def test_silent_wav(self, tmp_path: Path):
        wav_path = tmp_path / "test.wav"
        wav_data = _make_wav_bytes(duration=2.5, sample_rate=24000)
        wav_path.write_bytes(wav_data)
        dur = get_wav_duration(wav_path)
        assert abs(dur - 2.5) < 0.01

    def test_short_wav(self, tmp_path: Path):
        wav_path = tmp_path / "short.wav"
        wav_data = _make_wav_bytes(duration=0.1, sample_rate=24000)
        wav_path.write_bytes(wav_data)
        dur = get_wav_duration(wav_path)
        assert abs(dur - 0.1) < 0.01

    def test_missing_wav(self, tmp_path: Path):
        wav_path = tmp_path / "missing.wav"
        dur = get_wav_duration(wav_path)
        assert dur == 0.0

    def test_corrupt_wav(self, tmp_path: Path):
        wav_path = tmp_path / "corrupt.wav"
        wav_path.write_bytes(b"not a wav file at all")
        dur = get_wav_duration(wav_path)
        assert isinstance(dur, float)


# ============================================================
# TimelineBuilder tests
# ============================================================


class TestTimelineBuilder:
    def test_build_chapter(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        events = builder.build_chapter(0)

        assert len(events) > 0
        assert events[0].start_time == 0.0
        assert events[0].text == "お身体、大丈夫ですか？"
        assert events[0].segment_type == "narration"

        # 時系列の整合性
        for i in range(1, len(events)):
            assert events[i].start_time >= events[i - 1].end_time - 0.001
            assert events[i].end_time > events[i].start_time

    def test_build_chapter_1(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        events = builder.build_chapter(1)

        assert len(events) > 0
        assert events[0].speaker == "エナ"
        assert events[0].scene == "comedy"

    def test_build_all(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        timelines = builder.build_all()

        assert 0 in timelines
        assert 1 in timelines
        assert len(timelines[0]) + len(timelines[1]) > 0

    def test_duration_written_back(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        """タイムライン構築でdurationがmanifestに書き戻される."""
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        builder.build_chapter(0)

        for entry in manifest_with_wavs.sentences:
            if entry.chapter_index == 0 and entry.audio_file:
                assert entry.duration is not None
                assert entry.duration > 0

    def test_no_wav_files(
        self, manifest_no_wavs: BatchManifest, work_dir: Path
    ):
        """WAVファイルが無い場合は空タイムライン."""
        builder = TimelineBuilder(manifest_no_wavs, work_dir)
        events = builder.build_chapter(0)
        assert len(events) == 0

    def test_empty_chapter(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        events = builder.build_chapter(99)
        assert events == []

    def test_scene_break_included(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        """scene_breakもタイムラインに含まれる."""
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        events = builder.build_chapter(0)
        scene_breaks = [e for e in events if e.segment_type == "scene_break"]
        assert len(scene_breaks) >= 1


# ============================================================
# Subtitle helper tests
# ============================================================


class TestSubtitleHelpers:
    def test_format_ass_time(self):
        assert _format_ass_time(0.0) == "0:00:00.00"
        assert _format_ass_time(1.5) == "0:00:01.50"
        assert _format_ass_time(62.0) == "0:01:02.00"
        assert _format_ass_time(3661.0) == "1:01:01.00"

    def test_format_srt_time(self):
        assert _format_srt_time(0.0) == "00:00:00,000"
        assert _format_srt_time(1.5) == "00:00:01,500"
        assert _format_srt_time(62.0) == "00:01:02,000"
        assert _format_srt_time(3661.0) == "01:01:01,000"

    def test_hex_to_ass_color(self):
        assert _hex_to_ass_color("#FFFFFF") == "&H00FFFFFF"
        assert _hex_to_ass_color("#FF0000") == "&H000000FF"
        assert _hex_to_ass_color("#00FF00") == "&H0000FF00"
        assert _hex_to_ass_color("#0000FF") == "&H00FF0000"
        assert _hex_to_ass_color("#98D8C8") == "&H00C8D898"

    def test_wrap_text_short(self):
        text = "こんにちは"
        assert _wrap_text(text, 20) == text

    def test_wrap_text_at_punctuation(self):
        text = "お部屋が少し暑いですね。エアコン、つけましょうか。"
        result = _wrap_text(text, 15)
        assert "\\N" in result
        parts = result.split("\\N")
        assert all(len(p) <= 20 for p in parts)

    def test_wrap_text_no_punctuation(self):
        text = "あ" * 30
        result = _wrap_text(text, 10)
        assert "\\N" in result


# ============================================================
# SubtitleGenerator tests
# ============================================================


class TestSubtitleGenerator:
    def _make_events(self) -> list[TimelineEvent]:
        """ナースロボ＋エナの会話イベント."""
        events = []
        t = 0.0
        test_lines = _NURSEROBO_LINES[:3] + _ENA_LINES[:3]
        for i, (text, seg_type, speaker, scene, emotion) in enumerate(
            test_lines
        ):
            events.append(TimelineEvent(
                index=i,
                start_time=t,
                end_time=t + 1.5,
                text=text,
                speaker=speaker,
                scene=scene,
                emotion=emotion,
                intensity=0.7,
                segment_type=seg_type,
                audio_file=f"{i:04d}.wav",
            ))
            t += 1.5
        return events

    def test_generate_ass(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        gen = SubtitleGenerator(video_config)
        events = self._make_events()
        out = gen.generate_ass(
            events, tmp_path / "test.ass", title="テスト"
        )

        assert out.exists()
        content = out.read_text(encoding="utf-8-sig")

        assert "[Script Info]" in content
        assert "Title: テスト" in content
        assert "PlayResX: 640" in content
        assert "[V4+ Styles]" in content
        assert "Style: Narration" in content
        assert "Style: Dialogue" in content
        assert "Style: Thought" in content

        assert "[Events]" in content
        assert "お身体、大丈夫ですか？" in content
        assert "ナースロボ" in content
        assert "エナ" in content

        # ナースロボ色 #98D8C8 → BGR &H00C8D898
        assert "&H00C8D898" in content

    def test_generate_srt(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        gen = SubtitleGenerator(video_config)
        events = self._make_events()
        out = gen.generate_srt(events, tmp_path / "test.srt")

        assert out.exists()
        content = out.read_text(encoding="utf-8")

        lines = content.strip().split("\n")
        assert lines[0] == "1"
        assert "-->" in lines[1]
        assert "お身体、大丈夫ですか？" in lines[2]

        assert "[ナースロボ]" in content
        assert "[エナ]" in content

    def test_scene_break_excluded(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        """scene_breakは字幕に含まれない."""
        gen = SubtitleGenerator(video_config)
        events = [
            TimelineEvent(
                0, 0.0, 1.0, "テスト", None,
                "daily", "neutral", 0.5, "narration", "0000.wav",
            ),
            TimelineEvent(
                1, 1.0, 2.0, "", None,
                "daily", "neutral", 0.5, "scene_break", "0001.wav",
            ),
            TimelineEvent(
                2, 2.0, 3.0, "テスト2", None,
                "daily", "neutral", 0.5, "narration", "0002.wav",
            ),
        ]
        out = gen.generate_ass(events, tmp_path / "test.ass")
        content = out.read_text(encoding="utf-8-sig")

        dialogue_count = content.count("Dialogue:")
        assert dialogue_count == 2

    def test_speaker_auto_color(self, tmp_path: Path):
        """configに無い話者には自動色割当."""
        cfg = VideoConfig(resolution=(640, 360))
        gen = SubtitleGenerator(cfg)
        events = [
            TimelineEvent(
                0, 0.0, 1.0, "セリフ1", "太郎",
                "daily", "neutral", 0.5, "dialogue", "0000.wav",
            ),
            TimelineEvent(
                1, 1.0, 2.0, "セリフ2", "花子",
                "daily", "neutral", 0.5, "dialogue", "0001.wav",
            ),
            TimelineEvent(
                2, 2.0, 3.0, "セリフ3", "太郎",
                "daily", "neutral", 0.5, "dialogue", "0002.wav",
            ),
        ]
        out = gen.generate_ass(events, tmp_path / "auto.ass")
        content = out.read_text(encoding="utf-8-sig")
        assert "Dialogue:" in content

    def test_thought_italic(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        """思考セグメントはイタリック."""
        gen = SubtitleGenerator(video_config)
        events = [
            TimelineEvent(
                0, 0.0, 1.0, "心の声", None,
                "daily", "neutral", 0.5, "thought", "0000.wav",
            ),
        ]
        out = gen.generate_ass(events, tmp_path / "thought.ass")
        content = out.read_text(encoding="utf-8-sig")
        assert "Thought" in content
        thought_style = [
            line for line in content.split("\n")
            if "Style: Thought" in line
        ]
        assert len(thought_style) == 1
        assert ",1,0,0," in thought_style[0]


# ============================================================
# AssetManager tests
# ============================================================


class TestAssetManager:
    def test_no_background_returns_none(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        mgr = AssetManager(video_config, tmp_path)
        assert mgr.resolve_background("daily") is None

    def test_background_found(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        bg_dir = tmp_path / "assets" / "backgrounds"
        bg_dir.mkdir(parents=True)
        (bg_dir / "daily.jpg").write_bytes(b"fake jpg")
        mgr = AssetManager(video_config, tmp_path)
        assert mgr.resolve_background("daily") is not None
        assert "daily.jpg" in mgr.resolve_background("daily")

    def test_background_png(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        bg_dir = tmp_path / "assets" / "backgrounds"
        bg_dir.mkdir(parents=True)
        (bg_dir / "battle.png").write_bytes(b"fake png")
        mgr = AssetManager(video_config, tmp_path)
        assert mgr.resolve_background("battle") is not None

    def test_scene_color(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        mgr = AssetManager(video_config, tmp_path)
        assert mgr.get_scene_color("daily") == "#2C3E50"
        assert mgr.get_scene_color("battle") == "#8B0000"
        assert mgr.get_scene_color("comedy") == "#FFD700"
        assert mgr.get_scene_color("unknown") == "#2C3E50"

    def test_background_input_color(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        mgr = AssetManager(video_config, tmp_path)
        args = mgr.get_background_input("daily", 5.0)
        assert "-f" in args
        assert "lavfi" in args
        color_arg = [a for a in args if "color=" in a]
        assert len(color_arg) == 1
        assert "#2C3E50" in color_arg[0]

    def test_background_input_image(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        bg_dir = tmp_path / "assets" / "backgrounds"
        bg_dir.mkdir(parents=True)
        (bg_dir / "romance.jpg").write_bytes(b"fake jpg")
        mgr = AssetManager(video_config, tmp_path)
        args = mgr.get_background_input("romance", 5.0)
        assert "-loop" in args
        assert "1" in args


# ============================================================
# Manifest duration field (backward compatibility)
# ============================================================


class TestManifestDuration:
    def test_duration_field_default_none(self):
        entry = SentenceEntry(
            index=0, text="テスト", chapter_index=0,
            segment_type="narration",
        )
        assert entry.duration is None

    def test_duration_field_set(self):
        entry = SentenceEntry(
            index=0, text="テスト", chapter_index=0,
            segment_type="narration", duration=2.5,
        )
        assert entry.duration == 2.5

    def test_manifest_save_load_with_duration(self, tmp_path: Path):
        """durationフィールド付きmanifestの保存・読み込み."""
        manifest = BatchManifest(
            work_id="dur_test",
            work_title="Duration Test",
            source_url="file:///test",
            mode="voisona",
            sentences=[
                SentenceEntry(
                    index=0, text="テスト", chapter_index=0,
                    segment_type="narration", duration=1.5,
                    status="synthesized",
                ),
                SentenceEntry(
                    index=1, text="テスト2", chapter_index=0,
                    segment_type="dialogue", duration=None,
                    status="pending",
                ),
            ],
        )
        manifest.save(tmp_path)
        loaded = BatchManifest.load(tmp_path, "dur_test")
        assert loaded.sentences[0].duration == 1.5
        assert loaded.sentences[1].duration is None

    def test_manifest_load_without_duration(self, tmp_path: Path):
        """duration無しの旧形式manifestも読み込める（後方互換）."""
        out_dir = tmp_path / "old_test"
        out_dir.mkdir()
        data = {
            "work_id": "old_test",
            "work_title": "Old Format",
            "source_url": "file:///test",
            "mode": "voisona",
            "chapters": [],
            "characters": {},
            "sentences": [
                {
                    "index": 0,
                    "text": "テスト",
                    "chapter_index": 0,
                    "segment_type": "narration",
                    "status": "synthesized",
                },
            ],
            "analysis_complete": True,
            "synthesis_complete": True,
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(data, ensure_ascii=False)
        )
        loaded = BatchManifest.load(tmp_path, "old_test")
        assert loaded.sentences[0].duration is None


# ============================================================
# VideoComposer integration tests (requires ffmpeg)
# ============================================================


def _ffmpeg_available() -> bool:
    import subprocess

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available"
)
class TestVideoComposer:
    def test_compose_all(
        self,
        manifest_with_wavs: BatchManifest,
        work_dir: Path,
        video_config: VideoConfig,
    ):
        """全チャプター動画生成の統合テスト."""
        from yomiage.video.composer import VideoComposer

        base_dir = work_dir.parent
        manifest_with_wavs.save(base_dir)

        composer = VideoComposer(video_config, work_dir)
        result = composer.compose_all(manifest_with_wavs)

        assert result is not None
        assert result.exists()
        assert result.stat().st_size > 0
        assert result.suffix == ".mp4"

        ch1 = work_dir / "video" / "chapter_001.mp4"
        ch2 = work_dir / "video" / "chapter_002.mp4"
        assert ch1.exists()
        assert ch2.exists()

        ass1 = work_dir / "video" / "chapter_001.ass"
        ass2 = work_dir / "video" / "chapter_002.ass"
        assert ass1.exists()
        assert ass2.exists()

    def test_compose_single_chapter(
        self,
        manifest_with_wavs: BatchManifest,
        work_dir: Path,
        video_config: VideoConfig,
    ):
        """単一チャプター動画生成."""
        from yomiage.video.composer import VideoComposer
        from yomiage.video.timeline import TimelineBuilder

        base_dir = work_dir.parent
        manifest_with_wavs.save(base_dir)

        composer = VideoComposer(video_config, work_dir)
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        events = builder.build_chapter(0)

        result = composer.compose_chapter(manifest_with_wavs, 0, events)
        assert result is not None
        assert result.exists()
        assert result.stat().st_size > 0

    def test_subtitle_only(
        self,
        manifest_with_wavs: BatchManifest,
        work_dir: Path,
        video_config: VideoConfig,
    ):
        """字幕のみ生成."""
        from yomiage.video.subtitle import SubtitleGenerator
        from yomiage.video.timeline import TimelineBuilder

        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        sub_gen = SubtitleGenerator(video_config)
        timelines = builder.build_all()

        for ch_index, events in timelines.items():
            ass_path = (
                work_dir / "video" / f"chapter_{ch_index + 1:03d}.ass"
            )
            sub_gen.generate_ass(
                events, ass_path, title=f"Chapter {ch_index + 1}"
            )
            assert ass_path.exists()

            srt_path = (
                work_dir / "video" / f"chapter_{ch_index + 1:03d}.srt"
            )
            sub_gen.generate_srt(events, srt_path)
            assert srt_path.exists()

            srt_content = srt_path.read_text()
            assert "-->" in srt_content

    def test_multi_scene_detection(
        self,
        manifest_with_wavs: BatchManifest,
        work_dir: Path,
        video_config: VideoConfig,
    ):
        """複数シーンが正しく検出される."""
        from yomiage.video.composer import VideoComposer
        from yomiage.video.timeline import TimelineBuilder

        composer = VideoComposer(video_config, work_dir)
        builder = TimelineBuilder(manifest_with_wavs, work_dir)
        events = builder.build_chapter(0)

        segments = composer._detect_scene_segments(events)

        assert len(segments) >= 2
        for scene, start, end in segments:
            assert end >= start
            assert scene in (
                "daily", "tense", "battle", "comedy", "sad", "horror",
            )

    def test_empty_events(
        self, work_dir: Path, video_config: VideoConfig
    ):
        """空イベントでcompose_chapterはNone."""
        from yomiage.video.composer import VideoComposer

        manifest = BatchManifest(
            work_id="empty", work_title="Empty",
            source_url="", mode="voisona",
        )
        composer = VideoComposer(video_config, work_dir)
        result = composer.compose_chapter(manifest, 0, [])
        assert result is None


# ============================================================
# BatchEngine integration (video/subtitle methods)
# ============================================================


@pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available"
)
class TestBatchEngineVideo:
    def test_engine_subtitle(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        """BatchEngine.subtitle() の統合テスト."""
        from yomiage.batch.engine import BatchEngine

        base_dir = work_dir.parent
        manifest_with_wavs.save(base_dir)

        config = {
            "batch": {"output_dir": str(base_dir)},
            "video": {},
        }

        engine = BatchEngine.__new__(BatchEngine)
        engine.config = config
        engine.output_dir = base_dir

        results = engine.subtitle("test_hems_chars", fmt="ass")
        assert len(results) > 0
        for _ch_index, path in results.items():
            assert path.exists()
            assert path.suffix == ".ass"

        results = engine.subtitle("test_hems_chars", fmt="srt")
        assert len(results) > 0
        for _ch_index, path in results.items():
            assert path.exists()
            assert path.suffix == ".srt"

    def test_engine_video(
        self, manifest_with_wavs: BatchManifest, work_dir: Path
    ):
        """BatchEngine.video() の統合テスト."""
        from yomiage.batch.engine import BatchEngine

        base_dir = work_dir.parent
        manifest_with_wavs.save(base_dir)

        config = {
            "batch": {"output_dir": str(base_dir)},
            "video": {
                "resolution": [640, 360],
                "fps": 10,
                "crf": 30,
                "preset": "ultrafast",
                "subtitle": {"font_name": "sans-serif"},
            },
        }

        engine = BatchEngine.__new__(BatchEngine)
        engine.config = config
        engine.output_dir = base_dir

        result = engine.video("test_hems_chars")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp4"


# ============================================================
# Completion message tests (CONTEXT_AWARE_COMPLETION.md data)
# ============================================================


class TestCompletionMessages:
    """hems CONTEXT_AWARE_COMPLETION.md のメッセージを使った字幕テスト."""

    def test_completion_ass(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        events = []
        t = 0.0
        for i, (text, seg_type, speaker, scene, emotion) in enumerate(
            _COMPLETION_LINES
        ):
            events.append(TimelineEvent(
                index=i, start_time=t, end_time=t + 2.0,
                text=text, speaker=speaker, scene=scene,
                emotion=emotion, intensity=0.8,
                segment_type=seg_type, audio_file=f"{i:04d}.wav",
            ))
            t += 2.0

        gen = SubtitleGenerator(video_config)
        out = gen.generate_ass(
            events, tmp_path / "completion.ass", title="タスク完了"
        )
        content = out.read_text(encoding="utf-8-sig")

        assert "ありがとうございます" in content
        assert "コーヒー" in content
        assert "スムーズ" in content

    def test_completion_srt(
        self, video_config: VideoConfig, tmp_path: Path
    ):
        events = [
            TimelineEvent(
                0, 0.0, 4.0, _COMPLETION_LINES[0][0],
                None, "daily", "happy", 0.8, "narration", "0000.wav",
            ),
        ]
        gen = SubtitleGenerator(video_config)
        out = gen.generate_srt(events, tmp_path / "completion.srt")
        content = out.read_text()

        assert "00:00:00,000 --> 00:00:04,000" in content
        assert "気持ちよく過ごせます" in content
