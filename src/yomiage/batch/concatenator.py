"""Phase C: ffmpeg-based WAV concatenation."""

import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from .manifest import BatchManifest


class Concatenator:
    """ffmpeg concat demuxer でWAVファイルを結合."""

    def __init__(self, output_format: str = "wav", cleanup: bool = False):
        self.output_format = output_format
        self.cleanup = cleanup

    def concat_chapter(
        self, manifest: BatchManifest, chapter_index: int, base_dir: Path
    ) -> Path | None:
        """チャプター単位で結合."""
        work_dir = manifest.output_dir(base_dir)

        # このチャプターのWAVファイルを収集
        chapter_meta = None
        for ch in manifest.chapters:
            if ch.index == chapter_index:
                chapter_meta = ch
                break
        if not chapter_meta:
            logger.warning(f"Chapter {chapter_index} not found in manifest")
            return None

        wav_files = []
        for entry in manifest.sentences:
            if entry.chapter_index != chapter_index:
                continue
            if entry.status != "synthesized" or not entry.audio_file:
                continue
            wav_path = work_dir / entry.audio_file
            if wav_path.exists():
                wav_files.append(wav_path)

        if not wav_files:
            logger.warning(f"No WAV files for chapter {chapter_index}")
            return None

        output_name = f"chapter_{chapter_index + 1:03d}.{self.output_format}"
        output_path = work_dir / output_name
        self._concat_files(wav_files, output_path)

        if self.cleanup:
            for f in wav_files:
                f.unlink(missing_ok=True)

        logger.info(f"Chapter {chapter_index + 1} concatenated: {output_path}")
        return output_path

    def concat_all(self, manifest: BatchManifest, base_dir: Path) -> Path | None:
        """全チャプターを結合."""
        work_dir = manifest.output_dir(base_dir)

        # 全WAVファイルを連番順に収集
        wav_files = []
        for entry in sorted(manifest.sentences, key=lambda s: s.index):
            if entry.status != "synthesized" or not entry.audio_file:
                continue
            wav_path = work_dir / entry.audio_file
            if wav_path.exists():
                wav_files.append(wav_path)

        if not wav_files:
            logger.warning("No WAV files to concatenate")
            return None

        output_name = f"full.{self.output_format}"
        output_path = work_dir / output_name
        self._concat_files(wav_files, output_path)

        if self.cleanup:
            for f in wav_files:
                f.unlink(missing_ok=True)

        logger.info(f"Full concatenation: {output_path}")
        return output_path

    def concat_chapters_then_full(
        self, manifest: BatchManifest, base_dir: Path
    ) -> Path | None:
        """チャプター単位で結合後、全体を結合."""
        chapter_files = []
        for ch in manifest.chapters:
            ch_path = self.concat_chapter(manifest, ch.index, base_dir)
            if ch_path:
                chapter_files.append(ch_path)

        if not chapter_files:
            return None

        work_dir = manifest.output_dir(base_dir)
        output_name = f"full.{self.output_format}"
        output_path = work_dir / output_name

        if len(chapter_files) == 1:
            # 1チャプターのみなら単純コピー
            import shutil

            shutil.copy2(chapter_files[0], output_path)
        else:
            self._concat_files(chapter_files, output_path)

        if self.cleanup:
            # 個別WAVを削除（チャプターファイルは残す）
            for entry in manifest.sentences:
                if entry.audio_file:
                    f = work_dir / entry.audio_file
                    f.unlink(missing_ok=True)

        logger.info(f"Full output: {output_path}")
        return output_path

    def _concat_files(self, files: list[Path], output: Path) -> None:
        """ffmpeg concat demuxerで結合."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            for wav in files:
                # ffmpeg concat list format
                f.write(f"file '{wav.resolve()}'\n")
            concat_list = f.name

        try:
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list]

            if self.output_format == "mp3":
                cmd += ["-codec:a", "libmp3lame", "-q:a", "2"]
            elif self.output_format == "flac":
                cmd += ["-codec:a", "flac"]
            else:
                cmd += ["-codec:a", "pcm_s16le"]

            cmd.append(str(output))

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {result.stderr}")
        finally:
            Path(concat_list).unlink(missing_ok=True)
