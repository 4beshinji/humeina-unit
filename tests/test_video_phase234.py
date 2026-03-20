"""Phase 2-4 機能テスト: 立ち絵・BGM/SE・トランジション.

Office_as_AI_ToyBox の広報素材（SOMS LTスクリプト）を使い、
Phase 2（立ち絵オーバーレイ）、Phase 3（BGM/SEミキシング）、
Phase 4（xfadeトランジション・タイトルカード・Ken Burns）を検証する。

テスト用アセット（ポートレートPNG、BGM WAV、SE WAV）は
Pillow / wave で動的に生成し、tmp_path 内に配置する。
"""

import io
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from yomiage.batch.manifest import BatchManifest, ChapterMeta, SentenceEntry
from yomiage.video.asset_manager import AssetManager
from yomiage.video.audio_mixer import AudioMixer
from yomiage.video.composer import VideoComposer
from yomiage.video.config import (
    AudioConfig,
    BackgroundConfig,
    PortraitConfig,
    SubtitleConfig,
    TitleCardConfig,
    VideoConfig,
)
from yomiage.video.frame_builder import PortraitOverlay, TitleCardGenerator
from yomiage.video.timeline import TimelineBuilder, TimelineEvent

# ============================================================
# SOMS 広報 テストデータ (test_video_soms.py と同じ素材)
# ============================================================

_SOMS_LINES: list[tuple[str, str, str | None, str, str]] = [
    # Ch1: 問題提起
    (
        "オフィスにAIを住まわせたら家賃を請求された。",
        "narration", None, "comedy", "happy",
    ),
    (
        "スマートシティと名乗るシステムの多くは、"
        "建物の外に脳がある。",
        "narration", None, "tense", "neutral",
    ),
    (
        "脳が体の外にある生き物は、たぶん長生きできない。",
        "dialogue", "SOMS", "tense", "sad",
    ),
    (
        "発想の転換。データが生まれた建物の中で、"
        "全部処理すればいい。",
        "narration", None, "daily", "happy",
    ),
    # scene_break
    ("", "scene_break", None, "daily", "neutral"),
    # Ch2: Core Hub + デモ
    (
        "SOMS。建物を一匹の生き物にする。",
        "narration", None, "daily", "neutral",
    ),
    (
        "三十秒ごとに考えて、動いて、観察する。",
        "narration", None, "daily", "neutral",
    ),
    (
        "キッチンの換気をお願いします、千五百ポイントです。",
        "dialogue", "SOMS", "daily", "happy",
    ),
    (
        "AIに体がないのは仕様であって、バグではない。",
        "dialogue", "SOMS", "comedy", "happy",
    ),
    (
        "建物に脳を置く。都市に知性が宿る。",
        "dialogue", "SOMS", "daily", "happy",
    ),
]

_SOMS_SCENE_COLORS = {
    "daily": "#1E293B",
    "battle": "#0F172A",
    "tense": "#7F1D1D",
    "comedy": "#064E3B",
    "sad": "#1E1B4B",
}


# ============================================================
# ヘルパー
# ============================================================


def _make_wav_bytes(
    duration: float = 1.0, sample_rate: int = 24000
) -> bytes:
    n_frames = int(sample_rate * duration)
    data = struct.pack(f"<{n_frames}h", *([0] * n_frames))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)
    return buf.getvalue()


def _make_portrait_png(path: Path, color: str = "#34D399") -> None:
    """Pillow で単色RGBA PNGを生成（100x200 立ち絵ダミー）."""
    from PIL import Image

    img = Image.new("RGBA", (100, 200), color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))


def _build_manifest(
    work_dir: Path,
    lines: list[tuple[str, str, str | None, str, str]] | None = None,
) -> BatchManifest:
    """テスト用マニフェストを構築."""
    if lines is None:
        lines = _SOMS_LINES

    sentences: list[SentenceEntry] = []
    ch_break = None
    for i, (text, seg_type, *_) in enumerate(lines):
        if seg_type == "scene_break":
            ch_break = i
            break
    if ch_break is None:
        ch_break = len(lines)

    for i, (text, seg_type, speaker, scene, emotion) in enumerate(lines):
        chapter_index = 0 if i < ch_break else 1
        dur = max(0.5, len(text) * 0.08) if text else 0.3
        audio_file = f"{i:04d}.wav"
        wav_data = _make_wav_bytes(duration=dur)
        (work_dir / audio_file).write_bytes(wav_data)

        sentences.append(SentenceEntry(
            index=i,
            text=text,
            chapter_index=chapter_index,
            segment_type=seg_type,
            speaker=speaker,
            scene=scene,
            emotion=emotion,
            intensity=0.7 if emotion != "neutral" else 0.3,
            audio_file=audio_file,
            duration=dur,
            status="synthesized",
        ))

    return BatchManifest(
        work_id="soms_phase234",
        work_title="SOMS — 都市をAI化するアーキテクチャ",
        source_url="file:///Office_as_AI_ToyBox/docs/promo/",
        mode="voisona",
        chapters=[
            ChapterMeta(
                index=0, title="問題提起",
                url="file:///promo/slides_lt5.md",
                sentence_start=0, sentence_end=ch_break,
            ),
            ChapterMeta(
                index=1, title="Core Hub＋デモ",
                url="file:///promo/slides_lt5.md",
                sentence_start=ch_break, sentence_end=len(lines),
            ),
        ],
        characters={
            "SOMS": {
                "name": "SOMS",
                "gender": "unknown",
                "age_group": "adult",
                "personality": "建物に宿る自律型AI",
            },
        },
        sentences=sentences,
        analysis_complete=True,
        synthesis_complete=True,
    )


def _base_config(**overrides) -> VideoConfig:
    """テスト用低解像度VideoConfig."""
    defaults = dict(
        resolution=(640, 360),
        fps=10,
        crf=30,
        preset="ultrafast",
        subtitle=SubtitleConfig(
            font_size=24, font_name="sans-serif",
            outline_size=2, margin_bottom=30,
            max_chars_per_line=18,
            speaker_colors={
                "_narrator": "#E2E8F0",
                "_dialogue": "#34D399",
                "_thought": "#818CF8",
                "SOMS": "#34D399",
            },
        ),
        background=BackgroundConfig(
            scene_colors=_SOMS_SCENE_COLORS,
            transition="fade",
            transition_duration=0.0,  # テストではxfade無効
        ),
    )
    defaults.update(overrides)
    return VideoConfig(**defaults)


def _ffmpeg_available() -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "output" / "soms_phase234"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def manifest(work_dir: Path) -> BatchManifest:
    return _build_manifest(work_dir)


# ============================================================
# Phase 2: AssetManager — 立ち絵解決
# ============================================================


class TestAssetManagerPortrait:
    def test_resolve_portrait_exact_emotion(self, tmp_path: Path):
        """emotion指定で正確なPNGが見つかる."""
        cfg = _base_config()
        assets = tmp_path / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "happy.png", "#FFD700")
        _make_portrait_png(assets / "neutral.png", "#808080")

        mgr = AssetManager(cfg, tmp_path)
        path = mgr.resolve_portrait("SOMS", "happy")
        assert path is not None
        assert "happy.png" in path

    def test_resolve_portrait_fallback_neutral(self, tmp_path: Path):
        """指定emotionが無ければneutral.pngにフォールバック."""
        cfg = _base_config()
        assets = tmp_path / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "neutral.png")

        mgr = AssetManager(cfg, tmp_path)
        assert mgr.resolve_portrait("SOMS", "angry") is not None
        assert "neutral.png" in mgr.resolve_portrait("SOMS", "angry")

    def test_resolve_portrait_fallback_default(self, tmp_path: Path):
        """neutral.pngも無ければdefault.pngにフォールバック."""
        cfg = _base_config()
        assets = tmp_path / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "default.png")

        mgr = AssetManager(cfg, tmp_path)
        result = mgr.resolve_portrait("SOMS", "angry")
        assert result is not None
        assert "default.png" in result

    def test_resolve_portrait_missing_character(self, tmp_path: Path):
        """キャラディレクトリが無ければNone."""
        cfg = _base_config()
        mgr = AssetManager(cfg, tmp_path)
        assert mgr.resolve_portrait("Unknown", "neutral") is None

    def test_resolve_portrait_none_speaker(self, tmp_path: Path):
        """speakerがNoneならNone."""
        cfg = _base_config()
        mgr = AssetManager(cfg, tmp_path)
        assert mgr.resolve_portrait(None, "neutral") is None

    def test_get_portrait_for_event(self, tmp_path: Path):
        """TimelineEventから立ち絵パスを取得."""
        cfg = _base_config()
        assets = tmp_path / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "happy.png")

        mgr = AssetManager(cfg, tmp_path)
        event = TimelineEvent(
            0, 0.0, 1.0, "テスト", "SOMS",
            "daily", "happy", 0.7, "dialogue", "0000.wav",
        )
        assert mgr.get_portrait_for_event(event) is not None

    def test_get_portrait_for_narration(self, tmp_path: Path):
        """ナレーション（speaker=None）は立ち絵なし."""
        cfg = _base_config()
        mgr = AssetManager(cfg, tmp_path)
        event = TimelineEvent(
            0, 0.0, 1.0, "テスト", None,
            "daily", "neutral", 0.3, "narration", "0000.wav",
        )
        assert mgr.get_portrait_for_event(event) is None

    def test_collect_portraits(self, tmp_path: Path):
        """イベント列からユニーク立ち絵を収集."""
        cfg = _base_config()
        assets = tmp_path / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "happy.png")
        _make_portrait_png(assets / "sad.png")

        mgr = AssetManager(cfg, tmp_path)
        events = [
            TimelineEvent(
                0, 0.0, 1.0, "a", "SOMS",
                "daily", "happy", 0.7, "dialogue", "0.wav",
            ),
            TimelineEvent(
                1, 1.0, 2.0, "b", "SOMS",
                "tense", "sad", 0.7, "dialogue", "1.wav",
            ),
            TimelineEvent(
                2, 2.0, 3.0, "c", "SOMS",
                "daily", "happy", 0.7, "dialogue", "2.wav",
            ),
        ]
        portraits = mgr.collect_portraits(events)
        # happy + sad = 2 ユニークパス
        assert len(portraits) == 2


# ============================================================
# Phase 2: PortraitOverlay — filter_complex構築
# ============================================================


@pytest.mark.skipif(not _pillow_available(), reason="Pillow not installed")
class TestPortraitOverlay:
    def test_build_command_with_portraits(self, tmp_path: Path):
        """立ち絵ありでffmpegコマンドが構築される."""
        cfg = _base_config(style="portrait")
        assets = tmp_path / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "happy.png")

        mgr = AssetManager(cfg, tmp_path)
        overlay = PortraitOverlay(cfg, mgr)

        events = [
            TimelineEvent(
                0, 0.0, 1.0, "テスト", "SOMS",
                "daily", "happy", 0.7, "dialogue", "0.wav",
            ),
        ]
        bg = tmp_path / "bg.mp4"
        bg.write_bytes(b"")  # ダミー
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"")
        ass = tmp_path / "sub.ass"
        ass.write_text("")
        out = tmp_path / "out.mp4"

        cmd = overlay.build_overlay_command(
            events, bg, audio, ass, out
        )
        assert cmd is not None
        assert "ffmpeg" in cmd[0]
        assert "-filter_complex" in cmd
        # overlayフィルターが含まれる
        fc_idx = cmd.index("-filter_complex")
        fc = cmd[fc_idx + 1]
        assert "overlay" in fc
        assert "between" in fc

    def test_build_command_no_portraits(self, tmp_path: Path):
        """立ち絵無しならNone."""
        cfg = _base_config(style="portrait")
        mgr = AssetManager(cfg, tmp_path)
        overlay = PortraitOverlay(cfg, mgr)

        events = [
            TimelineEvent(
                0, 0.0, 1.0, "テスト", None,
                "daily", "neutral", 0.3, "narration", "0.wav",
            ),
        ]
        cmd = overlay.build_overlay_command(
            events, tmp_path / "bg.mp4", tmp_path / "a.wav",
            tmp_path / "s.ass", tmp_path / "out.mp4",
        )
        assert cmd is None

    def test_build_command_disabled(self, tmp_path: Path):
        """portrait.enabled=FalseならNone."""
        cfg = _base_config(
            style="portrait",
            portrait=PortraitConfig(enabled=False),
        )
        mgr = AssetManager(cfg, tmp_path)
        overlay = PortraitOverlay(cfg, mgr)
        cmd = overlay.build_overlay_command(
            [], tmp_path / "bg.mp4", tmp_path / "a.wav",
            tmp_path / "s.ass", tmp_path / "out.mp4",
        )
        assert cmd is None

    def test_position_bottom_left(self, tmp_path: Path):
        """position=bottom_leftでx座標が固定値."""
        cfg = _base_config(
            style="portrait",
            portrait=PortraitConfig(position="bottom_left", margin_x=30),
        )
        assets = tmp_path / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "neutral.png")

        mgr = AssetManager(cfg, tmp_path)
        overlay = PortraitOverlay(cfg, mgr)
        events = [
            TimelineEvent(
                0, 0.0, 1.0, "t", "SOMS",
                "daily", "neutral", 0.3, "dialogue", "0.wav",
            ),
        ]
        cmd = overlay.build_overlay_command(
            events, tmp_path / "bg.mp4", tmp_path / "a.wav",
            tmp_path / "s.ass", tmp_path / "out.mp4",
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "x=30" in fc


# ============================================================
# Phase 3: AssetManager — BGM/SE解決
# ============================================================


class TestAssetManagerAudio:
    def test_resolve_bgm_scene(self, tmp_path: Path):
        cfg = _base_config()
        bgm_dir = tmp_path / "assets" / "bgm"
        bgm_dir.mkdir(parents=True)
        (bgm_dir / "daily.wav").write_bytes(_make_wav_bytes(1.0))

        mgr = AssetManager(cfg, tmp_path)
        assert mgr.resolve_bgm("daily") is not None

    def test_resolve_bgm_fallback_default(self, tmp_path: Path):
        cfg = _base_config()
        bgm_dir = tmp_path / "assets" / "bgm"
        bgm_dir.mkdir(parents=True)
        (bgm_dir / "default.wav").write_bytes(_make_wav_bytes(1.0))

        mgr = AssetManager(cfg, tmp_path)
        # "tense"は無いがdefaultがある
        result = mgr.resolve_bgm("tense")
        assert result is not None
        assert "default" in result

    def test_resolve_bgm_missing(self, tmp_path: Path):
        cfg = _base_config()
        mgr = AssetManager(cfg, tmp_path)
        assert mgr.resolve_bgm("daily") is None

    def test_resolve_se(self, tmp_path: Path):
        cfg = _base_config()
        se_dir = tmp_path / "assets" / "se"
        se_dir.mkdir(parents=True)
        (se_dir / "scene_break.wav").write_bytes(_make_wav_bytes(0.3))

        mgr = AssetManager(cfg, tmp_path)
        assert mgr.resolve_se("scene_break") is not None

    def test_resolve_se_missing(self, tmp_path: Path):
        cfg = _base_config()
        mgr = AssetManager(cfg, tmp_path)
        assert mgr.resolve_se("scene_break") is None


# ============================================================
# Phase 3: AudioMixer
# ============================================================


@pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available"
)
class TestAudioMixer:
    def test_no_assets_returns_original(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """BGM/SEアセット無しなら元のTTS音声を返す."""
        cfg = _base_config(audio=AudioConfig(
            bgm_enabled=True, se_enabled=True,
        ))
        mgr = AssetManager(cfg, work_dir.parent.parent)
        mixer = AudioMixer(cfg, mgr)

        builder = TimelineBuilder(manifest, work_dir)
        events = builder.build_chapter(0)
        segments = [("daily", 0.0, events[-1].end_time)]

        tts = work_dir / "0000.wav"
        result = mixer.mix_chapter_audio(
            tts, events, segments,
            work_dir / "video" / "_mixed.wav",
        )
        # アセットが無いので元ファイルが返る
        assert result == tts

    def test_bgm_mixing(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """BGMミキシングで新しいWAVが生成される."""
        cfg = _base_config(audio=AudioConfig(
            bgm_enabled=True, bgm_volume=0.2,
        ))
        # BGMアセット作成
        base = work_dir.parent.parent
        bgm_dir = base / "assets" / "bgm"
        bgm_dir.mkdir(parents=True)
        (bgm_dir / "daily.wav").write_bytes(_make_wav_bytes(2.0))

        mgr = AssetManager(cfg, base)
        mixer = AudioMixer(cfg, mgr)

        # チャプター音声を結合
        builder = TimelineBuilder(manifest, work_dir)
        events = builder.build_chapter(1)
        segments = [("daily", 0.0, events[-1].end_time)]

        tts = work_dir / "chapter_002.wav"
        self._concat_wavs(work_dir, events, tts)

        out = work_dir / "video" / "_mixed.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        result = mixer.mix_chapter_audio(tts, events, segments, out)

        assert result != tts
        assert result.exists()
        assert result.stat().st_size > 0

    def test_se_insertion(
        self, work_dir: Path,
    ):
        """SE挿入で新しいWAVが生成される."""
        cfg = _base_config(audio=AudioConfig(se_enabled=True))
        base = work_dir.parent.parent
        se_dir = base / "assets" / "se"
        se_dir.mkdir(parents=True)
        (se_dir / "scene_break.wav").write_bytes(_make_wav_bytes(0.3))

        mgr = AssetManager(cfg, base)
        mixer = AudioMixer(cfg, mgr)

        # scene_breakを含むイベント列を直接構築
        events = [
            TimelineEvent(
                0, 0.0, 1.0, "テスト文A", None,
                "daily", "neutral", 0.3, "narration", "0000.wav",
            ),
            TimelineEvent(
                1, 1.0, 1.3, "", None,
                "daily", "neutral", 0.3, "scene_break", "0004.wav",
            ),
            TimelineEvent(
                2, 1.3, 2.3, "テスト文B", None,
                "daily", "neutral", 0.3, "narration", "0005.wav",
            ),
        ]
        segments = [("daily", 0.0, 2.3)]

        tts = work_dir / "_se_test_tts.wav"
        tts.write_bytes(_make_wav_bytes(2.3))

        out = work_dir / "video" / "_mixed_se.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        result = mixer.mix_chapter_audio(tts, events, segments, out)

        assert result != tts
        assert result.exists()

    def _concat_wavs(
        self, work_dir: Path,
        events: list[TimelineEvent], output: Path,
    ) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            for e in events:
                if e.audio_file:
                    p = work_dir / e.audio_file
                    if p.exists():
                        f.write(f"file '{p.resolve()}'\n")
            lst = f.name
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", lst, "-c:a", "pcm_s16le", str(output)],
            capture_output=True, timeout=30,
        )
        Path(lst).unlink(missing_ok=True)


# ============================================================
# Phase 4: TitleCardGenerator
# ============================================================


@pytest.mark.skipif(
    not _pillow_available() or not _ffmpeg_available(),
    reason="Pillow or ffmpeg not available",
)
class TestTitleCard:
    def test_generate_title_card(self, tmp_path: Path):
        """タイトルカードMP4が生成される."""
        cfg = _base_config(
            title_card=TitleCardConfig(
                enabled=True, duration=2.0,
                font_size=48, subtitle_font_size=24,
            ),
        )
        gen = TitleCardGenerator(cfg)
        result = gen.generate(
            "SOMS — 都市をAI化するアーキテクチャ",
            subtitle="問題提起",
            output_dir=tmp_path / "video",
        )
        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp4"
        assert result.stat().st_size > 0

    def test_disabled_returns_none(self, tmp_path: Path):
        cfg = _base_config(
            title_card=TitleCardConfig(enabled=False),
        )
        gen = TitleCardGenerator(cfg)
        assert gen.generate("Test", output_dir=tmp_path) is None

    def test_title_only(self, tmp_path: Path):
        """サブタイトル無しでも生成できる."""
        cfg = _base_config(
            title_card=TitleCardConfig(enabled=True, duration=1.0),
        )
        gen = TitleCardGenerator(cfg)
        result = gen.generate(
            "SOMS", output_dir=tmp_path / "video",
        )
        assert result is not None
        assert result.exists()


# ============================================================
# Phase 4: VideoConfig拡張テスト
# ============================================================


class TestPhase234Config:
    def test_portrait_config_defaults(self):
        cfg = VideoConfig()
        assert cfg.portrait.enabled is True
        assert cfg.portrait.position == "bottom_right"
        assert cfg.portrait.max_height_ratio == 0.7

    def test_audio_config_defaults(self):
        cfg = VideoConfig()
        assert cfg.audio.bgm_enabled is False
        assert cfg.audio.bgm_volume == 0.15
        assert cfg.audio.se_enabled is False

    def test_title_card_config_defaults(self):
        cfg = VideoConfig()
        assert cfg.title_card.enabled is False
        assert cfg.title_card.duration == 3.0

    def test_ken_burns_defaults(self):
        cfg = VideoConfig()
        assert cfg.background.ken_burns_enabled is False
        assert cfg.background.ken_burns_zoom == 1.2

    def test_style_default(self):
        cfg = VideoConfig()
        assert cfg.style == "subtitle"

    def test_from_dict_with_new_sections(self):
        cfg = VideoConfig.from_dict({
            "style": "portrait",
            "portrait": {"position": "bottom_left", "margin_x": 100},
            "audio": {"bgm_enabled": True, "bgm_volume": 0.2},
            "title_card": {"enabled": True, "duration": 5.0},
            "background": {
                "ken_burns_enabled": True,
                "ken_burns_zoom": 1.5,
            },
        })
        assert cfg.style == "portrait"
        assert cfg.portrait.position == "bottom_left"
        assert cfg.portrait.margin_x == 100
        assert cfg.audio.bgm_enabled is True
        assert cfg.audio.bgm_volume == 0.2
        assert cfg.title_card.enabled is True
        assert cfg.title_card.duration == 5.0
        assert cfg.background.ken_burns_enabled is True
        assert cfg.background.ken_burns_zoom == 1.5


# ============================================================
# Phase 2-4 VideoComposer 統合テスト
# ============================================================


@pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available"
)
class TestComposerIntegration:
    def test_subtitle_mode_unchanged(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """style=subtitle は従来通り動作."""
        cfg = _base_config()
        base_dir = work_dir.parent
        manifest.save(base_dir)

        composer = VideoComposer(cfg, work_dir)
        result = composer.compose_all(manifest)
        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp4"

    @pytest.mark.skipif(
        not _pillow_available(), reason="Pillow not installed"
    )
    def test_portrait_mode_with_assets(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """style=portrait + 立ち絵アセットありで動画生成."""
        cfg = _base_config(style="portrait")
        base = work_dir.parent.parent
        assets = base / "assets" / "portraits" / "SOMS"
        assets.mkdir(parents=True)
        _make_portrait_png(assets / "happy.png", "#34D399")
        _make_portrait_png(assets / "sad.png", "#818CF8")
        _make_portrait_png(assets / "neutral.png", "#808080")

        base_dir = work_dir.parent
        manifest.save(base_dir)

        composer = VideoComposer(cfg, work_dir)
        result = composer.compose_all(manifest)
        assert result is not None
        assert result.exists()

    def test_portrait_mode_no_assets_fallback(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """style=portrait でも立ち絵無しなら字幕モードにフォールバック."""
        cfg = _base_config(style="portrait")
        base_dir = work_dir.parent
        manifest.save(base_dir)

        composer = VideoComposer(cfg, work_dir)
        result = composer.compose_all(manifest)
        assert result is not None
        assert result.exists()

    def test_bgm_integration(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """BGM有効 + アセットありで動画生成."""
        cfg = _base_config(audio=AudioConfig(
            bgm_enabled=True, bgm_volume=0.15,
        ))
        base = work_dir.parent.parent
        bgm_dir = base / "assets" / "bgm"
        bgm_dir.mkdir(parents=True)
        (bgm_dir / "default.wav").write_bytes(_make_wav_bytes(2.0))

        base_dir = work_dir.parent
        manifest.save(base_dir)

        composer = VideoComposer(cfg, work_dir)
        result = composer.compose_all(manifest)
        assert result is not None
        assert result.exists()

    @pytest.mark.skipif(
        not _pillow_available(), reason="Pillow not installed"
    )
    def test_title_card_integration(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """タイトルカード有効で動画先頭に挿入."""
        cfg = _base_config(
            title_card=TitleCardConfig(
                enabled=True, duration=1.0,
            ),
        )
        base_dir = work_dir.parent
        manifest.save(base_dir)

        composer = VideoComposer(cfg, work_dir)
        result = composer.compose_all(manifest)
        assert result is not None
        assert result.exists()
        # タイトルカードMP4が生成されている
        tc = work_dir / "video" / "_title_card.mp4"
        assert tc.exists()

    def test_xfade_long_segments(self, tmp_path: Path):
        """十分に長いセグメントでxfadeが動作."""
        cfg = _base_config(
            background=BackgroundConfig(
                scene_colors=_SOMS_SCENE_COLORS,
                transition="fade",
                transition_duration=0.3,
            ),
        )
        work_dir = tmp_path / "output" / "xfade_test"
        work_dir.mkdir(parents=True)

        # 長いセグメント（各3秒）のデータを作成
        lines: list[tuple[str, str, str | None, str, str]] = [
            ("a" * 30, "narration", None, "daily", "neutral"),
            ("b" * 30, "narration", None, "tense", "neutral"),
            ("c" * 30, "narration", None, "comedy", "neutral"),
        ]
        m = _build_manifest(work_dir, lines)
        m.work_id = "xfade_test"
        m.chapters = [ChapterMeta(
            index=0, title="Test", url="",
            sentence_start=0, sentence_end=3,
        )]
        for s in m.sentences:
            s.chapter_index = 0
        base_dir = work_dir.parent
        m.save(base_dir)

        composer = VideoComposer(cfg, work_dir)
        result = composer.compose_all(m)
        assert result is not None
        assert result.exists()


# ============================================================
# Phase 2-4 BatchEngine統合テスト
# ============================================================


@pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available"
)
class TestBatchEnginePhase234:
    def test_engine_video_with_style(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """BatchEngine.video(style='portrait') のフォールバック."""
        from yomiage.batch.engine import BatchEngine

        base_dir = work_dir.parent
        manifest.save(base_dir)

        engine = BatchEngine.__new__(BatchEngine)
        engine.config = {"video": {
            "resolution": [640, 360],
            "fps": 10, "crf": 30, "preset": "ultrafast",
            "subtitle": {"font_name": "sans-serif"},
            "background": {
                "scene_colors": _SOMS_SCENE_COLORS,
                "transition_duration": 0.0,
            },
        }}
        engine.output_dir = base_dir

        result = engine.video("soms_phase234", style="portrait")
        assert result is not None
        assert result.exists()

    def test_engine_video_default_style(
        self, work_dir: Path, manifest: BatchManifest
    ):
        """style未指定ではsubtitleモード."""
        from yomiage.batch.engine import BatchEngine

        base_dir = work_dir.parent
        manifest.save(base_dir)

        engine = BatchEngine.__new__(BatchEngine)
        engine.config = {"video": {
            "resolution": [640, 360],
            "fps": 10, "crf": 30, "preset": "ultrafast",
            "subtitle": {"font_name": "sans-serif"},
            "background": {"transition_duration": 0.0},
        }}
        engine.output_dir = base_dir

        result = engine.video("soms_phase234")
        assert result is not None
        assert result.exists()
