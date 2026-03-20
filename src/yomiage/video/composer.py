"""VideoComposer — Phase D orchestrator for video generation."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from ..batch.manifest import BatchManifest
from .asset_manager import AssetManager
from .config import VideoConfig
from .subtitle import SubtitleGenerator
from .timeline import TimelineBuilder, TimelineEvent


class VideoComposer:
    """動画生成オーケストレーター.

    manifest + WAVファイル → ASS字幕 + ffmpeg → MP4
    Phase 1: 字幕動画
    Phase 2: 立ち絵オーバーレイ
    Phase 3: BGM/SEミキシング
    Phase 4: トランジション・タイトルカード
    """

    def __init__(self, config: VideoConfig, work_dir: Path):
        self.config = config
        self.work_dir = work_dir
        self.video_dir = work_dir / "video"
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.asset_manager = AssetManager(config, work_dir.parent.parent)
        self.subtitle_gen = SubtitleGenerator(config)

    def compose_chapter(
        self,
        manifest: BatchManifest,
        chapter_index: int,
        events: list[TimelineEvent],
    ) -> Path | None:
        """チャプター単位で動画を生成."""
        if not events:
            return None

        total_duration = events[-1].end_time

        ch_title = ""
        for ch in manifest.chapters:
            if ch.index == chapter_index:
                ch_title = ch.title
                break

        # 1. 字幕生成（チャプタータイトルオーバーレイ付き）
        ass_path = self.video_dir / f"chapter_{chapter_index + 1:03d}.ass"
        self.subtitle_gen.generate_ass(
            events, ass_path,
            title=f"{manifest.work_title} - {ch_title}",
            chapter_title=ch_title,
        )

        # 2. チャプター音声
        chapter_audio = self.work_dir / f"chapter_{chapter_index + 1:03d}.wav"
        if not chapter_audio.exists():
            chapter_audio = self._concat_chapter_audio(events)
            if not chapter_audio:
                logger.warning(f"No audio for chapter {chapter_index}")
                return None

        # 3. BGM/SEミキシング (Phase 3)
        scene_segments = self._detect_scene_segments(events)
        chapter_audio = self._mix_audio(
            chapter_audio, events, scene_segments
        )

        # 4. 動画合成
        output = self.video_dir / f"chapter_{chapter_index + 1:03d}.mp4"
        self._compose_video(
            events, chapter_audio, ass_path, output, total_duration
        )

        return output

    def compose_all(self, manifest: BatchManifest) -> Path | None:
        """全チャプターの動画を生成し結合."""
        builder = TimelineBuilder(manifest, self.work_dir)
        timelines = builder.build_all()

        if not timelines:
            logger.warning("No timeline events found")
            return None

        chapter_videos: list[Path] = []
        for ch_index, events in sorted(timelines.items()):
            video = self.compose_chapter(manifest, ch_index, events)
            if video:
                chapter_videos.append(video)

        manifest.save(self.work_dir.parent)

        if not chapter_videos:
            logger.warning("No chapter videos generated")
            return None

        # タイトルカード (Phase 4)
        title_video = self._generate_title_card(
            manifest.work_title,
            subtitle=manifest.chapters[0].title if manifest.chapters else "",
        )

        all_videos = (
            [title_video] + chapter_videos if title_video
            else chapter_videos
        )

        if len(all_videos) == 1:
            full_output = self.video_dir / "full.mp4"
            shutil.copy2(all_videos[0], full_output)
            logger.info(f"Video output: {full_output}")
            return full_output

        return self._concat_videos(all_videos)

    # --- Phase 3: Audio mixing ---

    def _mix_audio(
        self,
        tts_audio: Path,
        events: list[TimelineEvent],
        scene_segments: list[tuple[str, float, float]],
    ) -> Path:
        """BGM/SEが有効ならミキシング."""
        cfg = self.config.audio
        if not cfg.bgm_enabled and not cfg.se_enabled:
            return tts_audio

        from .audio_mixer import AudioMixer

        mixer = AudioMixer(self.config, self.asset_manager)
        mixed_path = self.video_dir / "_mixed_audio.wav"
        return mixer.mix_chapter_audio(
            tts_audio, events, scene_segments, mixed_path
        )

    # --- Video composition ---

    def _compose_video(
        self,
        events: list[TimelineEvent],
        audio_path: Path,
        ass_path: Path,
        output: Path,
        total_duration: float,
    ) -> None:
        """ffmpegで動画を合成."""
        scene_segments = self._detect_scene_segments(events)
        use_xfade = (
            len(scene_segments) > 1
            and self.config.background.transition_duration > 0
        )

        if len(scene_segments) <= 1:
            scene = scene_segments[0][0] if scene_segments else "daily"
            bg_video = self._make_bg_video(
                scene, total_duration
            )
        else:
            bg_video = self._make_multi_scene_bg(
                scene_segments, use_xfade
            )

        if bg_video is None:
            raise RuntimeError("Failed to generate background video")

        # Phase 2: 立ち絵オーバーレイ
        composed = self._try_portrait_overlay(
            events, bg_video, audio_path, ass_path, output
        )
        if composed:
            bg_video.unlink(missing_ok=True)
            return

        # フォールバック: 字幕のみ
        self._overlay_subtitle_and_audio(
            bg_video, audio_path, ass_path, output
        )
        bg_video.unlink(missing_ok=True)

    def _make_bg_video(
        self, scene: str, duration: float
    ) -> Path | None:
        """単一シーンの背景動画を生成."""
        width, height = self.config.resolution
        bg_path = self.asset_manager.resolve_background(scene)
        color = self.asset_manager.get_scene_color(scene)
        output = self.video_dir / "_bg_single.mp4"

        cmd = ["ffmpeg", "-y"]
        if bg_path:
            cmd += ["-loop", "1", "-i", bg_path]
            vf = self._bg_scale_filter(width, height, duration, bg_path)
            cmd += ["-vf", vf, "-t", f"{duration:.3f}"]
        else:
            cmd += [
                "-f", "lavfi", "-i",
                f"color=c={color}:s={width}x{height}"
                f":d={duration:.3f}:r={self.config.fps}",
            ]

        cmd += [
            "-c:v", self.config.codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-an",
            str(output),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            logger.error(f"BG video failed: {result.stderr[-300:]}")
            return None
        return output

    def _bg_scale_filter(
        self, width: int, height: int, duration: float,
        bg_path: str,
    ) -> str:
        """背景画像のスケーリングフィルター.

        Ken Burns有効時はzoompanを使用。
        """
        bg_cfg = self.config.background
        if bg_cfg.ken_burns_enabled and bg_path:
            zoom = bg_cfg.ken_burns_zoom
            frames = int(duration * self.config.fps)
            zoom_step = (zoom - 1.0) / max(frames, 1)
            return (
                f"zoompan=z='min(zoom+{zoom_step:.6f},{zoom})':"
                f"d={frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={width}x{height}:fps={self.config.fps}"
            )
        return (
            f"scale={width}:{height}"
            f":force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )

    def _make_multi_scene_bg(
        self,
        scene_segments: list[tuple[str, float, float]],
        use_xfade: bool = False,
    ) -> Path | None:
        """複数シーンの背景動画を生成."""
        width, height = self.config.resolution
        segment_videos: list[Path] = []
        seg_durations: list[float] = []

        for i, (scene, start, end) in enumerate(scene_segments):
            seg_duration = end - start
            if seg_duration <= 0:
                continue

            seg_path = self.video_dir / f"_seg_{i:04d}.mp4"
            bg_path = self.asset_manager.resolve_background(scene)
            color = self.asset_manager.get_scene_color(scene)

            cmd = ["ffmpeg", "-y"]
            if bg_path:
                cmd += ["-loop", "1", "-i", bg_path]
                vf = self._bg_scale_filter(
                    width, height, seg_duration, bg_path
                )
                cmd += ["-vf", vf, "-t", f"{seg_duration:.3f}"]
            else:
                cmd += [
                    "-f", "lavfi", "-i",
                    f"color=c={color}:s={width}x{height}"
                    f":d={seg_duration:.3f}:r={self.config.fps}",
                ]

            cmd += [
                "-c:v", self.config.codec,
                "-crf", str(self.config.crf),
                "-preset", self.config.preset,
                "-an",
                str(seg_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                logger.error(f"Segment {i} failed: {result.stderr[-300:]}")
                continue
            segment_videos.append(seg_path)
            seg_durations.append(seg_duration)

        if not segment_videos:
            return None

        # Phase 4: xfadeトランジション
        # xfadeは全セグメントがtransition_durationより長い場合のみ使用
        trans_dur = self.config.background.transition_duration
        can_xfade = (
            use_xfade
            and len(segment_videos) > 1
            and all(d > trans_dur * 2 for d in seg_durations)
        )
        if can_xfade:
            output = self._xfade_segments(
                segment_videos, seg_durations
            )
        else:
            output = self._concat_segment_videos(segment_videos)

        for seg in segment_videos:
            seg.unlink(missing_ok=True)

        return output

    def _xfade_segments(
        self,
        videos: list[Path],
        durations: list[float],
    ) -> Path | None:
        """xfadeでシーン切替トランジション (Phase 4)."""
        trans = self.config.background.transition
        trans_dur = self.config.background.transition_duration

        # xfade対応トランジション
        valid_transitions = {
            "fade", "wipeleft", "wiperight", "dissolve",
            "pixelize", "slidedown", "slideleft",
        }
        if trans not in valid_transitions:
            trans = "fade"

        output = self.video_dir / "_bg_combined.mp4"
        cmd = ["ffmpeg", "-y"]
        for v in videos:
            cmd += ["-i", str(v)]

        # filter_complex: チェーンxfade
        filters: list[str] = []
        current = "[0:v]"
        cumulative = durations[0]

        for i in range(1, len(videos)):
            offset = cumulative - trans_dur
            if offset < 0:
                offset = 0
            out_label = f"[xf{i}]"
            filters.append(
                f"{current}[{i}:v]xfade="
                f"transition={trans}:"
                f"duration={trans_dur:.3f}:"
                f"offset={offset:.3f}"
                f"{out_label}"
            )
            current = out_label
            cumulative += durations[i] - trans_dur

        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", current,
            "-c:v", self.config.codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-an",
            str(output),
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            logger.warning(
                f"xfade failed, falling back to concat: "
                f"{result.stderr[-200:]}"
            )
            return self._concat_segment_videos(videos)

        return output

    def _concat_segment_videos(self, videos: list[Path]) -> Path | None:
        """セグメント動画をconcatで結合."""
        output = self.video_dir / "_bg_combined.mp4"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            dir=str(self.video_dir),
        ) as f:
            for seg in videos:
                f.write(f"file '{seg.resolve()}'\n")
            concat_list = f.name

        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c", "copy",
                str(output),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                logger.error(
                    f"BG concat failed: {result.stderr[-300:]}"
                )
                return None
        finally:
            Path(concat_list).unlink(missing_ok=True)

        return output

    # --- Phase 2: Portrait overlay ---

    def _try_portrait_overlay(
        self,
        events: list[TimelineEvent],
        bg_video: Path,
        audio_path: Path,
        ass_path: Path,
        output: Path,
    ) -> bool:
        """立ち絵オーバーレイを試行. 成功したらTrue."""
        if self.config.style != "portrait":
            return False

        from .frame_builder import PortraitOverlay

        overlay = PortraitOverlay(self.config, self.asset_manager)
        cmd = overlay.build_overlay_command(
            events, bg_video, audio_path, ass_path, output
        )
        if not cmd:
            return False

        logger.debug(f"Portrait overlay: {len(cmd)} args")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            logger.warning(
                f"Portrait overlay failed, fallback to subtitle: "
                f"{result.stderr[-200:]}"
            )
            return False

        logger.info(f"Video (portrait): {output}")
        return True

    # --- Phase 4: Title card ---

    def _generate_title_card(
        self, title: str, subtitle: str = ""
    ) -> Path | None:
        """タイトルカードを生成."""
        if not self.config.title_card.enabled:
            return None

        from .frame_builder import TitleCardGenerator

        gen = TitleCardGenerator(self.config)
        return gen.generate(
            title, subtitle=subtitle, output_dir=self.video_dir
        )

    # --- Subtitle + audio overlay ---

    def _overlay_subtitle_and_audio(
        self,
        bg_video: Path,
        audio_path: Path,
        ass_path: Path,
        output: Path,
    ) -> None:
        """背景動画に字幕と音声を合成."""
        ass_escaped = (
            str(ass_path.resolve())
            .replace("\\", "\\\\")
            .replace(":", "\\:")
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(bg_video),
            "-i", str(audio_path),
            "-vf", f"ass='{ass_escaped}'",
            "-c:v", self.config.codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Subtitle overlay failed: {result.stderr[-500:]}"
            )
        logger.info(f"Video: {output}")

    # --- Common helpers ---

    def _concat_chapter_audio(
        self, events: list[TimelineEvent]
    ) -> Path | None:
        """イベントのWAVファイルを結合."""
        wav_files: list[Path] = []
        for event in events:
            if event.audio_file:
                wav_path = self.work_dir / event.audio_file
                if wav_path.exists():
                    wav_files.append(wav_path)

        if not wav_files:
            return None

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            dir=str(self.video_dir),
        ) as f:
            for wav in wav_files:
                f.write(f"file '{wav.resolve()}'\n")
            concat_list = f.name

        output = self.video_dir / "_chapter_audio.wav"
        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_list,
                "-codec:a", "pcm_s16le",
                str(output),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                logger.error(f"Audio concat failed: {result.stderr}")
                return None
        finally:
            Path(concat_list).unlink(missing_ok=True)

        return output

    def _detect_scene_segments(
        self, events: list[TimelineEvent]
    ) -> list[tuple[str, float, float]]:
        """イベント列からシーン区間を検出."""
        if not events:
            return [("daily", 0.0, 0.0)]

        segments: list[tuple[str, float, float]] = []
        current_scene = events[0].scene
        segment_start = 0.0

        for event in events:
            if event.scene != current_scene:
                segments.append(
                    (current_scene, segment_start, event.start_time)
                )
                current_scene = event.scene
                segment_start = event.start_time

        segments.append(
            (current_scene, segment_start, events[-1].end_time)
        )
        return segments

    def _concat_videos(self, videos: list[Path]) -> Path:
        """複数チャプター動画を結合."""
        output = self.video_dir / "full.mp4"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            dir=str(self.video_dir),
        ) as f:
            for v in videos:
                f.write(f"file '{v.resolve()}'\n")
            concat_list = f.name

        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c", "copy",
                "-movflags", "+faststart",
                str(output),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Video concat failed: {result.stderr[-500:]}"
                )
        finally:
            Path(concat_list).unlink(missing_ok=True)

        logger.info(f"Full video: {output}")
        return output
