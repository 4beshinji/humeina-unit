"""Phase C: ffmpeg-based WAV concatenation."""

from pathlib import Path

from loguru import logger

from ..application.operations import ThreadProcessRunner
from ..tts.audio_utils import concat_wav_files
from .manifest import BatchManifest


class Concatenator:
    """ffmpeg concat demuxer でWAVファイルを結合."""

    def __init__(
        self,
        output_format: str = "wav",
        cleanup: bool = False,
        *,
        process_runner: ThreadProcessRunner | None = None,
    ):
        self.output_format = output_format
        self.cleanup = cleanup
        self.process_runner = process_runner

    def concat_chapter(
        self,
        manifest: BatchManifest,
        chapter_index: int,
        base_dir: Path,
        *,
        allow_partial: bool = False,
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

        if not allow_partial and self._chapter_has_unresolved_audio(
            manifest, chapter_index, work_dir
        ):
            logger.warning(
                "Refusing to concatenate chapter %s: synthesis is incomplete",
                chapter_index,
            )
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

        partial = allow_partial and manifest.compute_completion_status(base_dir) == "partial"
        suffix = ".partial" if partial else ""
        output_name = f"chapter_{chapter_index + 1:03d}{suffix}.{self.output_format}"
        output_path = work_dir / output_name
        self._concat_files(wav_files, output_path)

        if self.cleanup:
            for f in wav_files:
                f.unlink(missing_ok=True)

        logger.info(f"Chapter {chapter_index + 1} concatenated: {output_path}")
        return output_path

    def concat_all(
        self,
        manifest: BatchManifest,
        base_dir: Path,
        *,
        allow_partial: bool = False,
    ) -> Path | None:
        """全チャプターを結合."""
        work_dir = manifest.output_dir(base_dir)

        if not allow_partial and self._has_unresolved_audio(manifest, work_dir):
            logger.warning("Refusing full concatenation: synthesis is incomplete")
            return None

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

        partial = allow_partial and manifest.compute_completion_status(base_dir) == "partial"
        suffix = ".partial" if partial else ""
        output_name = f"full{suffix}.{self.output_format}"
        output_path = work_dir / output_name
        self._concat_files(wav_files, output_path)

        if self.cleanup:
            for f in wav_files:
                f.unlink(missing_ok=True)

        logger.info(f"Full concatenation: {output_path}")
        return output_path

    def concat_chapters_then_full(
        self,
        manifest: BatchManifest,
        base_dir: Path,
        *,
        allow_partial: bool = False,
    ) -> Path | None:
        """チャプター単位で結合後、全体を結合."""
        status = manifest.refresh_completion_status(base_dir)
        if status in {"partial", "failed"} and not allow_partial:
            logger.warning(
                "Refusing full concatenation for %s manifest without allow_partial",
                status,
            )
            manifest.save(base_dir)
            return None
        if status == "failed":
            logger.warning("Cannot concatenate a manifest with no usable audio")
            manifest.save(base_dir)
            return None

        chapter_files = []
        for ch in manifest.chapters:
            ch_path = self.concat_chapter(
                manifest,
                ch.index,
                base_dir,
                allow_partial=allow_partial,
            )
            if ch_path:
                chapter_files.append(ch_path)

        if not chapter_files:
            return None

        work_dir = manifest.output_dir(base_dir)
        partial = status == "partial"
        suffix = ".partial" if partial else ""
        output_name = f"full{suffix}.{self.output_format}"
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

        # concat結果と同じ世代の完全性状態をmanifestへ公開する。
        manifest.save(base_dir)
        logger.info(f"Full output: {output_path}")
        return output_path

    def _concat_files(self, files: list[Path], output: Path) -> None:
        kwargs = {}
        if self.process_runner is not None:
            kwargs["process_runner"] = self.process_runner
        concat_wav_files(files, output, self.output_format, **kwargs)

    @staticmethod
    def _chapter_has_unresolved_audio(
        manifest: BatchManifest, chapter_index: int, work_dir: Path
    ) -> bool:
        """指定チャプターに未合成または欠損音声があるか確認する."""
        for entry in manifest.sentences:
            if entry.chapter_index != chapter_index:
                continue
            if entry.status not in {"synthesized", "skipped"}:
                return True
            if (
                entry.status == "synthesized"
                and entry.audio_file
                and not (work_dir / entry.audio_file).is_file()
            ):
                return True
        return False

    @staticmethod
    def _has_unresolved_audio(manifest: BatchManifest, work_dir: Path) -> bool:
        """manifest全体に未合成または欠損音声があるか確認する."""
        return bool(manifest.unresolved_sentences or manifest.missing_audio_files(work_dir.parent))
