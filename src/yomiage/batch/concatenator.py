"""Phase C: ffmpeg-based WAV concatenation."""

from pathlib import Path

from loguru import logger

from ..tts.audio_utils import concat_wav_files
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
        concat_wav_files(wav_files, output_path, self.output_format)

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
        concat_wav_files(wav_files, output_path, self.output_format)

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
            concat_wav_files(chapter_files, output_path, self.output_format)

        if self.cleanup:
            # 個別WAVを削除（チャプターファイルは残す）
            for entry in manifest.sentences:
                if entry.audio_file:
                    f = work_dir / entry.audio_file
                    f.unlink(missing_ok=True)

        logger.info(f"Full output: {output_path}")
        return output_path
