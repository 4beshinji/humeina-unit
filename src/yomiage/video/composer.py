"""VideoComposer — Phase D orchestrator for video generation."""

from __future__ import annotations

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

        # チャプターメタ取得
        ch_title = ""
        for ch in manifest.chapters:
            if ch.index == chapter_index:
                ch_title = ch.title
                break

        # 1. 字幕生成
        ass_path = self.video_dir / f"chapter_{chapter_index + 1:03d}.ass"
        self.subtitle_gen.generate_ass(
            events, ass_path, title=f"{manifest.work_title} - {ch_title}"
        )

        # 2. チャプター音声を結合（既存のchapter WAVがあればそれを使う）
        chapter_audio = self.work_dir / f"chapter_{chapter_index + 1:03d}.wav"
        if not chapter_audio.exists():
            chapter_audio = self._concat_chapter_audio(events)
            if not chapter_audio:
                logger.warning(f"No audio for chapter {chapter_index}")
                return None

        # 3. シーン別背景を解決してffmpegで合成
        output = self.video_dir / f"chapter_{chapter_index + 1:03d}.mp4"
        self._compose_video(events, chapter_audio, ass_path, output, total_duration)
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

        # durationをmanifestに書き戻し
        manifest.save(self.work_dir.parent)

        if not chapter_videos:
            logger.warning("No chapter videos generated")
            return None

        if len(chapter_videos) == 1:
            full_output = self.video_dir / "full.mp4"
            import shutil
            shutil.copy2(chapter_videos[0], full_output)
            logger.info(f"Video output: {full_output}")
            return full_output

        # 複数チャプターを結合
        return self._concat_videos(chapter_videos)

    def _concat_chapter_audio(self, events: list[TimelineEvent]) -> Path | None:
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
            mode="w", suffix=".txt", delete=False, dir=str(self.video_dir)
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error(f"Audio concat failed: {result.stderr}")
                return None
        finally:
            Path(concat_list).unlink(missing_ok=True)

        return output

    def _compose_video(
        self,
        events: list[TimelineEvent],
        audio_path: Path,
        ass_path: Path,
        output: Path,
        total_duration: float,
    ) -> None:
        """ffmpegで背景+音声+字幕→MP4を合成."""
        width, height = self.config.resolution

        # シーン区間を検出
        scene_segments = self._detect_scene_segments(events)

        if len(scene_segments) <= 1:
            # 単一シーン: シンプルなffmpegコマンド
            scene = scene_segments[0][0] if scene_segments else "daily"
            self._compose_single_scene(
                scene, audio_path, ass_path, output, total_duration
            )
        else:
            # 複数シーン: シーン別背景を切り替え
            self._compose_multi_scene(
                scene_segments, audio_path, ass_path, output, total_duration
            )

    def _detect_scene_segments(
        self, events: list[TimelineEvent]
    ) -> list[tuple[str, float, float]]:
        """イベント列からシーン区間を検出.

        Returns: [(scene, start_time, end_time), ...]
        """
        if not events:
            return [("daily", 0.0, 0.0)]

        segments: list[tuple[str, float, float]] = []
        current_scene = events[0].scene
        segment_start = 0.0

        for event in events:
            if event.scene != current_scene:
                segments.append((current_scene, segment_start, event.start_time))
                current_scene = event.scene
                segment_start = event.start_time

        # 最後のセグメント
        segments.append((current_scene, segment_start, events[-1].end_time))
        return segments

    def _compose_single_scene(
        self,
        scene: str,
        audio_path: Path,
        ass_path: Path,
        output: Path,
        total_duration: float,
    ) -> None:
        """単一シーンの動画合成."""
        width, height = self.config.resolution
        bg_path = self.asset_manager.resolve_background(scene)
        color = self.asset_manager.get_scene_color(scene)

        cmd = ["ffmpeg", "-y"]

        # 背景入力
        if bg_path:
            cmd += ["-loop", "1", "-i", bg_path]
        else:
            cmd += [
                "-f", "lavfi",
                "-i",
                f"color=c={color}:s={width}x{height}:d={total_duration:.3f}"
                f":r={self.config.fps}",
            ]

        # 音声入力
        cmd += ["-i", str(audio_path)]

        # 画像入力の場合スケーリングが必要
        vf_filters: list[str] = []
        if bg_path:
            vf_filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
            vf_filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")

        # ASS字幕を焼き込み
        # ASSパスのバックスラッシュとコロンをエスケープ
        ass_escaped = str(ass_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")
        vf_filters.append(f"ass='{ass_escaped}'")

        cmd += ["-vf", ",".join(vf_filters)]

        # エンコード設定
        cmd += [
            "-c:v", self.config.codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output),
        ]

        logger.debug(f"ffmpeg command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Video compose failed: {result.stderr[-500:]}")
        logger.info(f"Video: {output}")

    def _compose_multi_scene(
        self,
        scene_segments: list[tuple[str, float, float]],
        audio_path: Path,
        ass_path: Path,
        output: Path,
        total_duration: float,
    ) -> None:
        """複数シーンの動画合成（シーン別背景切替）.

        各シーン区間の背景を個別に生成し、concatで結合後に字幕を合成。
        """
        width, height = self.config.resolution
        segment_videos: list[Path] = []

        # 各シーン区間の背景動画を生成
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
                vf = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
                )
                cmd += [
                    "-vf", vf,
                    "-t", f"{seg_duration:.3f}",
                ]
            else:
                cmd += [
                    "-f", "lavfi",
                    "-i",
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error(f"Segment {i} failed: {result.stderr[-300:]}")
                continue
            segment_videos.append(seg_path)

        if not segment_videos:
            # フォールバック: 単一シーンとして処理
            self._compose_single_scene(
                "daily", audio_path, ass_path, output, total_duration
            )
            return

        # セグメント動画をconcat
        bg_video = self.video_dir / "_bg_combined.mp4"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, dir=str(self.video_dir)
        ) as f:
            for seg in segment_videos:
                f.write(f"file '{seg.resolve()}'\n")
            concat_list = f.name

        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c", "copy",
                str(bg_video),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(f"Background concat failed: {result.stderr[-300:]}")
        finally:
            Path(concat_list).unlink(missing_ok=True)

        # 背景動画 + 音声 + 字幕を合成
        ass_escaped = str(ass_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Final compose failed: {result.stderr[-500:]}")

        # 一時ファイル削除
        for seg in segment_videos:
            seg.unlink(missing_ok=True)
        bg_video.unlink(missing_ok=True)

        logger.info(f"Video (multi-scene): {output}")

    def _concat_videos(self, videos: list[Path]) -> Path:
        """複数チャプター動画を結合."""
        output = self.video_dir / "full.mp4"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, dir=str(self.video_dir)
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise RuntimeError(f"Video concat failed: {result.stderr[-500:]}")
        finally:
            Path(concat_list).unlink(missing_ok=True)

        logger.info(f"Full video: {output}")
        return output
