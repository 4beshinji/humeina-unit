"""SOMS広報素材を使った動画生成テスト.

Office_as_AI_ToyBox の promo/ ドキュメントセットから抽出した
ライトニングトーク＋記事コンテンツで全パイプラインを検証する。

構成:
  Chapter 1: 問題提起 — スマートシティの構造的課題
  Chapter 2: Core Hub — 建物を一匹の生き物にする
  Chapter 3: デモ＋経済 — CO2検知から解決まで
  Chapter 4: ビジョン — 1オフィスから都市へ
"""

import io
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from yomiage.batch.manifest import BatchManifest, ChapterMeta, SentenceEntry
from yomiage.video.composer import VideoComposer
from yomiage.video.config import BackgroundConfig, SubtitleConfig, VideoConfig
from yomiage.video.subtitle import SubtitleGenerator
from yomiage.video.timeline import TimelineBuilder

# ============================================================
# SOMS 広報素材テストデータ
# ============================================================

# (text, segment_type, speaker, scene, emotion)
# scene: daily=通常解説, tense=問題提起, comedy=デモ/冗談, battle=技術,
#        sad=データ主権の深刻さ

_CH1_PROBLEM: list[tuple[str, str, str | None, str, str]] = [
    # Slide 1: タイトル
    (
        "オフィスにAIを住まわせたら家賃を請求された。",
        "narration", None, "comedy", "happy",
    ),
    (
        "GPU一台。Docker十二サービス。クラウド月額ゼロドル。",
        "narration", None, "daily", "neutral",
    ),
    # Slide 2: 問題提起
    (
        "スマートシティと名乗るシステムの多くは、建物の外に脳がある。",
        "narration", None, "tense", "neutral",
    ),
    (
        "リアルタイムAI制御をうたいながら、"
        "クラウド往復で数百ミリ秒の遅延。",
        "narration", None, "tense", "angry",
    ),
    (
        "自治体が自分のデータをAPI経由で購入する構造。",
        "narration", None, "tense", "angry",
    ),
    (
        "カメラ映像の保管権限が外部ベンダーに帰属している。",
        "narration", None, "tense", "angry",
    ),
    (
        "脳が体の外にある生き物は、たぶん長生きできない。",
        "dialogue", "SOMS", "tense", "sad",
    ),
    (
        "発想の転換。データが生まれた建物の中で、全部処理すればいい。",
        "narration", None, "daily", "happy",
    ),
]

_CH2_COREHUB: list[tuple[str, str, str | None, str, str]] = [
    # Slide 3: SOMS = 生き物
    (
        "SOMS。建物を一匹の生き物にする。",
        "narration", None, "daily", "neutral",
    ),
    (
        "GPU一台のサーバーが脳。センサーとカメラが感覚器官。"
        "MQTTが神経系。",
        "narration", None, "daily", "neutral",
    ),
    (
        "三十秒ごとに考えて、動いて、観察する。",
        "narration", None, "daily", "neutral",
    ),
    (
        "正常なら何もしない。異常を見つけたら、人間にお願いする。",
        "dialogue", "SOMS", "daily", "neutral",
    ),
    # 技術スタック
    (
        "脳はQwen二点五、十四B。Ollamaで毎秒五十一トークン。"
        "応答三点三秒。",
        "narration", None, "battle", "neutral",
    ),
    (
        "視覚はYOLOv11。座りすぎ検知で健康アドバイス。",
        "narration", None, "battle", "neutral",
    ),
    (
        "声はVOICEVOX。拒否ストック百件を事前生成。"
        "無視されると拗ねる。",
        "narration", None, "comedy", "happy",
    ),
    (
        "経済は複式簿記プラスデマレッジ。貯めると減る。使うと増える。",
        "narration", None, "battle", "neutral",
    ),
]

_CH3_DEMO: list[tuple[str, str, str | None, str, str]] = [
    # Slide 4: CO2デモ
    (
        "三十秒で起きること。CO2が上がった日。",
        "narration", None, "daily", "neutral",
    ),
    (
        "ESP32がCO2千五十ppmを検知。MQTTでWorldModelが更新される。",
        "narration", None, "tense", "neutral",
    ),
    (
        "高いな。三人いるし、換気しよう。",
        "thought", "SOMS", "tense", "neutral",
    ),
    (
        "キッチンの換気をお願いします、千五百ポイントです。",
        "dialogue", "SOMS", "daily", "happy",
    ),
    (
        "AIは判断する。人間は動く。ちゃんと払う。",
        "narration", None, "daily", "neutral",
    ),
    (
        "窓の開け方をAPIで教える必要はない。",
        "dialogue", "SOMS", "comedy", "happy",
    ),
    # Slide 5: 経済圏
    (
        "AIがお金を配る経済圏。",
        "narration", None, "daily", "neutral",
    ),
    (
        "窓を開けてください。これはAPIコールでは解決できない。",
        "dialogue", "SOMS", "tense", "neutral",
    ),
    (
        "タスク報酬は五百から五千ポイント。"
        "難易度と緊急度でLLMが値付けする。",
        "narration", None, "daily", "neutral",
    ),
    (
        "全自動ではなく共生。AIの知性と人間の身体性の分業。",
        "narration", None, "daily", "neutral",
    ),
    (
        "AIに体がないのは仕様であって、バグではない。",
        "dialogue", "SOMS", "comedy", "happy",
    ),
]

_CH4_VISION: list[tuple[str, str, str | None, str, str]] = [
    # Slide 6: 数字
    (
        "数字で見るCore Hub。",
        "narration", None, "daily", "neutral",
    ),
    (
        "データ圧縮比、五万対一。"
        "五十ギガバイトの生データから、外部送信はたった一メガバイト。",
        "narration", None, "battle", "neutral",
    ),
    (
        "映像保存時間、ゼロ秒。YOLO推論後に即破棄。GDPRの最適解。",
        "narration", None, "sad", "neutral",
    ),
    (
        "クラウド月額ゼロドル。ランニングコストはGPU一台の電気代のみ。",
        "narration", None, "daily", "happy",
    ),
    (
        "Hub撤去イコール全データ消失。これが物理的なデータ主権。",
        "narration", None, "sad", "neutral",
    ),
    # Slide 7: ビジョン
    (
        "ビジョン。一オフィスから都市へ。",
        "narration", None, "daily", "neutral",
    ),
    (
        "各Hubが一時間平均のCO2値を送信するだけで、"
        "都市の人流が浮かび上がる。",
        "narration", None, "daily", "neutral",
    ),
    (
        "住宅街、朝七時。オフィス街、九時。"
        "商業施設、夕方。住宅街、夜。",
        "narration", None, "daily", "neutral",
    ),
    (
        "送信データは数バイト。プライバシーコストはゼロ。",
        "narration", None, "daily", "neutral",
    ),
    # クロージング
    (
        "オフィスにAIを住まわせたら、けっこう良い同居人だった。",
        "narration", None, "comedy", "happy",
    ),
    (
        "Phase 0、動作中。コードは全部公開。",
        "narration", None, "daily", "neutral",
    ),
    (
        "建物に脳を置く。都市に知性が宿る。",
        "dialogue", "SOMS", "daily", "happy",
    ),
]

# シーン別の意味的背景色（SOMS ブランドカラー）
_SOMS_SCENE_COLORS = {
    "daily": "#1E293B",    # slate-800 (ベース)
    "battle": "#0F172A",   # slate-900 (技術解説)
    "tense": "#7F1D1D",    # red-900 (問題提起)
    "comedy": "#064E3B",   # emerald-900 (ユーモア)
    "sad": "#1E1B4B",      # indigo-950 (データ主権)
    "romance": "#581C87",  # purple-900 (未使用)
    "horror": "#0C0A09",   # stone-950 (未使用)
}


def _make_wav_bytes(
    duration: float = 1.0, sample_rate: int = 24000
) -> bytes:
    """テスト用サイレントWAV."""
    n_frames = int(sample_rate * duration)
    data = struct.pack(f"<{n_frames}h", *([0] * n_frames))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data)
    return buf.getvalue()


def _build_soms_manifest(
    work_dir: Path,
    wav_duration: float = 1.0,
) -> BatchManifest:
    """SOMS広報コンテンツからマニフェストを構築."""
    chapters_data = [
        ("問題提起：スマートシティの構造的課題", _CH1_PROBLEM),
        ("Core Hub：建物を一匹の生き物にする", _CH2_COREHUB),
        ("デモ＋経済：CO2検知から解決まで", _CH3_DEMO),
        ("ビジョン：1オフィスから都市へ", _CH4_VISION),
    ]

    sentences: list[SentenceEntry] = []
    chapters: list[ChapterMeta] = []
    global_idx = 0

    for ch_i, (ch_title, lines) in enumerate(chapters_data):
        ch_start = global_idx
        for text, seg_type, speaker, scene, emotion in lines:
            audio_file = f"{global_idx:04d}.wav"
            # テキスト長に比例した duration (1文字≒0.1秒、最低0.5秒)
            dur = max(0.5, len(text) * 0.1)

            entry = SentenceEntry(
                index=global_idx,
                text=text,
                chapter_index=ch_i,
                segment_type=seg_type,
                speaker=speaker,
                scene=scene,
                emotion=emotion,
                intensity=0.7 if emotion != "neutral" else 0.3,
                audio_file=audio_file,
                duration=dur,
                status="synthesized",
            )
            sentences.append(entry)

            wav_data = _make_wav_bytes(duration=dur)
            (work_dir / audio_file).write_bytes(wav_data)
            global_idx += 1

        chapters.append(ChapterMeta(
            index=ch_i,
            title=ch_title,
            url="file:///Office_as_AI_ToyBox/docs/promo/",
            sentence_start=ch_start,
            sentence_end=global_idx,
        ))

    return BatchManifest(
        work_id="soms_promo",
        work_title="SOMS — 都市をAI化するアーキテクチャ",
        source_url="file:///Office_as_AI_ToyBox/docs/promo/slides_lt5.md",
        mode="voisona",
        chapters=chapters,
        characters={
            "SOMS": {
                "name": "SOMS",
                "gender": "unknown",
                "age_group": "adult",
                "personality": "建物に宿る自律型AI。冷静で論理的だが、"
                "時折ユーモアを交える。",
            },
        },
        sentences=sentences,
        analysis_complete=True,
        synthesis_complete=True,
    )


def _soms_video_config() -> VideoConfig:
    """SOMS広報用VideoConfig（テスト向け低解像度）."""
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
            max_chars_per_line=18,
            speaker_colors={
                "_narrator": "#E2E8F0",   # slate-200
                "_dialogue": "#34D399",   # emerald-400 (SOMS voice)
                "_thought": "#818CF8",    # indigo-400
                "SOMS": "#34D399",
            },
        ),
        background=BackgroundConfig(
            scene_colors=_SOMS_SCENE_COLORS,
            transition="fade",
            transition_duration=0.5,
        ),
    )


@pytest.fixture
def soms_work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "output" / "soms_promo"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def soms_manifest(soms_work_dir: Path) -> BatchManifest:
    return _build_soms_manifest(soms_work_dir)


@pytest.fixture
def soms_config() -> VideoConfig:
    return _soms_video_config()


def _ffmpeg_available() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ============================================================
# タイムライン構築テスト
# ============================================================


class TestSomsTimeline:
    def test_four_chapters(
        self, soms_manifest: BatchManifest, soms_work_dir: Path
    ):
        """4チャプター全てのタイムラインが構築される."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        timelines = builder.build_all()
        assert len(timelines) == 4

    def test_chapter_event_count(
        self, soms_manifest: BatchManifest, soms_work_dir: Path
    ):
        """各チャプターのイベント数がソースデータと一致."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        timelines = builder.build_all()

        assert len(timelines[0]) == len(_CH1_PROBLEM)
        assert len(timelines[1]) == len(_CH2_COREHUB)
        assert len(timelines[2]) == len(_CH3_DEMO)
        assert len(timelines[3]) == len(_CH4_VISION)

    def test_timing_continuity(
        self, soms_manifest: BatchManifest, soms_work_dir: Path
    ):
        """各チャプター内の時系列が連続している."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        for _ch_i, events in builder.build_all().items():
            assert events[0].start_time == 0.0
            for i in range(1, len(events)):
                assert events[i].start_time == pytest.approx(
                    events[i - 1].end_time, abs=0.01
                )

    def test_duration_proportional_to_text(
        self, soms_manifest: BatchManifest, soms_work_dir: Path
    ):
        """長いテキストほどdurationが長い."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        events = builder.build_chapter(0)
        # タイトル(短)と問題提起(長)を比較
        short_ev = events[0]   # "オフィスにAIを住まわせたら..."
        long_ev = events[3]    # "リアルタイムAI制御を..."
        # 短いテキストのdurationは長いテキストより短い
        assert (
            short_ev.end_time - short_ev.start_time
            <= long_ev.end_time - long_ev.start_time
        )

    def test_scene_variety(
        self, soms_manifest: BatchManifest, soms_work_dir: Path
    ):
        """複数のシーンタイプが使われている."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        all_scenes = set()
        for events in builder.build_all().values():
            for e in events:
                all_scenes.add(e.scene)
        # daily, tense, comedy, battle, sad が最低限含まれる
        assert {"daily", "tense", "comedy", "battle", "sad"} <= all_scenes

    def test_speaker_distribution(
        self, soms_manifest: BatchManifest, soms_work_dir: Path
    ):
        """ナレーションとSOMS発話が混在."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        speakers = set()
        seg_types = set()
        for events in builder.build_all().values():
            for e in events:
                speakers.add(e.speaker)
                seg_types.add(e.segment_type)
        assert None in speakers       # ナレーター
        assert "SOMS" in speakers      # AIキャラクター
        assert "narration" in seg_types
        assert "dialogue" in seg_types
        assert "thought" in seg_types  # SOSMの思考


# ============================================================
# 字幕生成テスト
# ============================================================


class TestSomsSubtitle:
    def test_ass_all_chapters(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """全4チャプターのASS字幕を生成."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        gen = SubtitleGenerator(soms_config)
        timelines = builder.build_all()

        for ch_i, events in timelines.items():
            ch_title = soms_manifest.chapters[ch_i].title
            out = soms_work_dir / "video" / f"chapter_{ch_i + 1:03d}.ass"
            gen.generate_ass(
                events, out,
                title=f"SOMS — {ch_title}",
            )
            assert out.exists()

            content = out.read_text(encoding="utf-8-sig")
            assert "[Script Info]" in content
            assert "[Events]" in content
            assert "PlayResX: 640" in content

    def test_ass_soms_branding(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """SOMS発話にブランドカラーが適用される."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        gen = SubtitleGenerator(soms_config)
        events = builder.build_chapter(0)

        out = soms_work_dir / "video" / "ch1_branding.ass"
        gen.generate_ass(events, out)
        content = out.read_text(encoding="utf-8-sig")

        # SOMS emerald #34D399 → BGR: 99D334 → &H0099D334
        assert "&H0099D334" in content

    def test_ass_thought_segment(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """SOSMの思考がThoughtスタイルで出力される."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        gen = SubtitleGenerator(soms_config)
        # Chapter 3にthoughtがある
        events = builder.build_chapter(2)

        out = soms_work_dir / "video" / "ch3_thought.ass"
        gen.generate_ass(events, out)
        content = out.read_text(encoding="utf-8-sig")

        # "高いな。三人いるし、換気しよう" → Thought style
        assert "換気しよう" in content
        thought_lines = [
            line for line in content.split("\n")
            if "Dialogue:" in line and "Thought" in line
        ]
        assert len(thought_lines) >= 1

    def test_srt_all_chapters(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """全4チャプターのSRT字幕を生成."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        gen = SubtitleGenerator(soms_config)
        timelines = builder.build_all()

        for ch_i, events in timelines.items():
            out = soms_work_dir / "video" / f"chapter_{ch_i + 1:03d}.srt"
            gen.generate_srt(events, out)
            assert out.exists()

            content = out.read_text(encoding="utf-8")
            assert "-->" in content

    def test_srt_speaker_prefix(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """SRTでSOMS発話に話者プレフィックスが付く."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        gen = SubtitleGenerator(soms_config)
        events = builder.build_chapter(0)

        out = soms_work_dir / "video" / "ch1_prefix.srt"
        gen.generate_srt(events, out)
        content = out.read_text()

        assert "[SOMS]" in content

    def test_srt_key_phrases(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """重要キーフレーズがSRTに含まれる."""
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        gen = SubtitleGenerator(soms_config)

        all_srt = ""
        for events in builder.build_all().values():
            out = soms_work_dir / "video" / "combined.srt"
            gen.generate_srt(events, out)
            all_srt += out.read_text()

        # SOMS広報の重要フレーズ
        key_phrases = [
            "スマートシティ",
            "建物の外に脳",
            "一匹の生き物",
            "千五百ポイント",
            "五万対一",
            "一オフィスから都市へ",
            "物理的なデータ主権",
        ]
        for phrase in key_phrases:
            assert phrase in all_srt, f"Missing key phrase: {phrase}"


# ============================================================
# VideoComposer 統合テスト
# ============================================================


@pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available"
)
class TestSomsVideoComposer:
    def test_compose_all_chapters(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """全4チャプターMP4＋結合fullを生成."""
        base_dir = soms_work_dir.parent
        soms_manifest.save(base_dir)

        composer = VideoComposer(soms_config, soms_work_dir)
        result = composer.compose_all(soms_manifest)

        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp4"
        assert result.stat().st_size > 0

        # 全チャプター個別ファイル
        for i in range(4):
            mp4 = soms_work_dir / "video" / f"chapter_{i + 1:03d}.mp4"
            ass = soms_work_dir / "video" / f"chapter_{i + 1:03d}.ass"
            assert mp4.exists(), f"Missing chapter_{i + 1:03d}.mp4"
            assert ass.exists(), f"Missing chapter_{i + 1:03d}.ass"

    def test_chapter_scene_segments(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """各チャプターで複数シーン背景切替が発生する."""
        composer = VideoComposer(soms_config, soms_work_dir)
        builder = TimelineBuilder(soms_manifest, soms_work_dir)

        # Ch1: comedy→daily→tense→daily (最低3セグメント)
        ev1 = builder.build_chapter(0)
        seg1 = composer._detect_scene_segments(ev1)
        assert len(seg1) >= 3, (
            f"Ch1 expected >=3 segments, got {len(seg1)}: "
            f"{[s[0] for s in seg1]}"
        )

        # Ch2: daily→battle→comedy→battle (最低3セグメント)
        ev2 = builder.build_chapter(1)
        seg2 = composer._detect_scene_segments(ev2)
        assert len(seg2) >= 3

        # Ch3: daily→tense→daily→comedy→... (最低4セグメント)
        ev3 = builder.build_chapter(2)
        seg3 = composer._detect_scene_segments(ev3)
        assert len(seg3) >= 4

    def test_single_chapter_video(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """単一チャプター（Ch3: デモ）の動画生成."""
        base_dir = soms_work_dir.parent
        soms_manifest.save(base_dir)

        composer = VideoComposer(soms_config, soms_work_dir)
        builder = TimelineBuilder(soms_manifest, soms_work_dir)
        events = builder.build_chapter(2)

        result = composer.compose_chapter(soms_manifest, 2, events)
        assert result is not None
        assert result.exists()
        assert result.stat().st_size > 0

    def test_manifest_duration_persisted(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """compose_all後にmanifestのdurationが保存される."""
        base_dir = soms_work_dir.parent
        soms_manifest.save(base_dir)

        composer = VideoComposer(soms_config, soms_work_dir)
        composer.compose_all(soms_manifest)

        # manifestを再読み込み
        loaded = BatchManifest.load(base_dir, "soms_promo")
        for entry in loaded.sentences:
            assert entry.duration is not None
            assert entry.duration > 0

    def test_video_file_sizes_reasonable(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
        soms_config: VideoConfig,
    ):
        """生成されたMP4のサイズが妥当な範囲."""
        base_dir = soms_work_dir.parent
        soms_manifest.save(base_dir)

        composer = VideoComposer(soms_config, soms_work_dir)
        composer.compose_all(soms_manifest)

        full = soms_work_dir / "video" / "full.mp4"
        assert full.exists()
        size_kb = full.stat().st_size / 1024
        # 640x360 ultrafast crf30 の無音動画 → 数十KB〜数百KB
        assert size_kb > 10, f"Too small: {size_kb:.1f} KB"
        assert size_kb < 10000, f"Too large: {size_kb:.1f} KB"


# ============================================================
# BatchEngine統合テスト
# ============================================================


@pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available"
)
class TestSomsBatchEngine:
    def test_engine_subtitle_ass(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
    ):
        """BatchEngine.subtitle() → ASS 4チャプター."""
        from yomiage.batch.engine import BatchEngine

        base_dir = soms_work_dir.parent
        soms_manifest.save(base_dir)

        engine = BatchEngine.__new__(BatchEngine)
        engine.config = {"video": {
            "resolution": [640, 360],
            "subtitle": {
                "font_name": "sans-serif",
                "speaker_colors": {
                    "_narrator": "#E2E8F0",
                    "_dialogue": "#34D399",
                    "_thought": "#818CF8",
                    "SOMS": "#34D399",
                },
            },
            "background": {"scene_colors": _SOMS_SCENE_COLORS},
        }}
        engine.output_dir = base_dir

        results = engine.subtitle("soms_promo", fmt="ass")
        assert len(results) == 4

        for ch_i, path in results.items():
            assert path.exists()
            content = path.read_text(encoding="utf-8-sig")
            assert "Dialogue:" in content

    def test_engine_subtitle_srt(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
    ):
        """BatchEngine.subtitle() → SRT 4チャプター."""
        from yomiage.batch.engine import BatchEngine

        base_dir = soms_work_dir.parent
        soms_manifest.save(base_dir)

        engine = BatchEngine.__new__(BatchEngine)
        engine.config = {"video": {}}
        engine.output_dir = base_dir

        results = engine.subtitle("soms_promo", fmt="srt")
        assert len(results) == 4

    def test_engine_video(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
    ):
        """BatchEngine.video() → MP4."""
        from yomiage.batch.engine import BatchEngine

        base_dir = soms_work_dir.parent
        soms_manifest.save(base_dir)

        engine = BatchEngine.__new__(BatchEngine)
        engine.config = {"video": {
            "resolution": [640, 360],
            "fps": 10,
            "crf": 30,
            "preset": "ultrafast",
            "subtitle": {"font_name": "sans-serif"},
            "background": {"scene_colors": _SOMS_SCENE_COLORS},
        }}
        engine.output_dir = base_dir

        result = engine.video("soms_promo")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp4"


# ============================================================
# コンテンツ完全性テスト
# ============================================================


class TestSomsContentIntegrity:
    """広報素材の内容が正しく動画パイプラインに反映されること."""

    def test_total_sentence_count(self, soms_manifest: BatchManifest):
        total = (
            len(_CH1_PROBLEM)
            + len(_CH2_COREHUB)
            + len(_CH3_DEMO)
            + len(_CH4_VISION)
        )
        assert len(soms_manifest.sentences) == total

    def test_all_sentences_synthesized(
        self, soms_manifest: BatchManifest
    ):
        for entry in soms_manifest.sentences:
            assert entry.status == "synthesized"

    def test_character_soms_exists(
        self, soms_manifest: BatchManifest
    ):
        assert "SOMS" in soms_manifest.characters
        char = soms_manifest.characters["SOMS"]
        assert "自律型AI" in char["personality"]

    def test_chapter_titles(self, soms_manifest: BatchManifest):
        titles = [ch.title for ch in soms_manifest.chapters]
        assert "問題提起" in titles[0]
        assert "Core Hub" in titles[1]
        assert "デモ" in titles[2]
        assert "ビジョン" in titles[3]

    def test_wav_files_exist(
        self,
        soms_manifest: BatchManifest,
        soms_work_dir: Path,
    ):
        """全てのWAVファイルが存在する."""
        for entry in soms_manifest.sentences:
            if entry.audio_file:
                wav = soms_work_dir / entry.audio_file
                assert wav.exists(), f"Missing: {entry.audio_file}"

    def test_segment_type_distribution(
        self, soms_manifest: BatchManifest
    ):
        """narration/dialogue/thoughtが適切に分布."""
        types = [s.segment_type for s in soms_manifest.sentences]
        assert types.count("narration") > types.count("dialogue")
        assert types.count("dialogue") > 0
        assert types.count("thought") > 0

    def test_emotion_variety(self, soms_manifest: BatchManifest):
        emotions = {s.emotion for s in soms_manifest.sentences}
        assert "neutral" in emotions
        assert "happy" in emotions
        assert "angry" in emotions
        assert "sad" in emotions
