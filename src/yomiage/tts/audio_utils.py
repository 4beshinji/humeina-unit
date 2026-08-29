"""WAV オーディオ共通ユーティリティ.

無音生成・結合・duration 取得など、TTS/バッチ/動画モジュール間で
散在していた処理を 1 箇所に集約する.
"""

from __future__ import annotations

import io
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ..application.operations import ThreadProcessRunner
from ..utils.atomic_io import atomic_replace_file, atomic_write_wav

if TYPE_CHECKING:
    pass


@dataclass(frozen=True, slots=True)
class AudioDescriptor:
    """公開前に検証したaudio streamの構造。"""

    format: str
    sample_rate: int | None = None
    channels: int | None = None
    sample_width: int | None = None
    frames: int | None = None


def validate_audio_bytes(audio_data: bytes, declared_format: str = "wav") -> AudioDescriptor:
    """bytesと宣言formatの整合・完全性を検証する。"""

    data = bytes(audio_data)
    audio_format = str(declared_format).lower().lstrip(".")
    if not data:
        raise ValueError("audio data is empty")
    if audio_format == "wav":
        try:
            with wave.open(io.BytesIO(data), "rb") as stream:
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                sample_rate = stream.getframerate()
                frames = stream.getnframes()
                compression = stream.getcomptype()
                payload = stream.readframes(frames)
            expected = frames * channels * sample_width
            if (
                data[:4] != b"RIFF"
                or data[8:12] != b"WAVE"
                or compression != "NONE"
                or channels <= 0
                or sample_width <= 0
                or sample_rate <= 0
                or frames <= 0
                or len(payload) != expected
            ):
                raise ValueError("invalid or truncated PCM WAV structure")
        except (EOFError, OSError, wave.Error, ValueError) as exc:
            raise ValueError("invalid or truncated WAV audio") from exc
        return AudioDescriptor("wav", sample_rate, channels, sample_width, frames)
    signatures = {
        "flac": (b"fLaC",),
        "ogg": (b"OggS",),
        "mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    }
    expected_signatures = signatures.get(audio_format)
    if expected_signatures is None:
        raise ValueError(f"unsupported audio format: {audio_format}")
    if not any(data.startswith(signature) for signature in expected_signatures):
        raise ValueError(f"audio bytes do not match declared format: {audio_format}")
    return AudioDescriptor(audio_format)


def write_silence_wav(
    path: Path, duration: float, sample_rate: int = 24000
) -> None:
    """無音 WAV ファイルを書き出し.

    フォーマット: PCM 16bit, mono.
    """
    num_samples = int(sample_rate * duration)
    data_size = num_samples * 2  # 16-bit mono

    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))  # PCM
    buf.write(struct.pack("<H", 1))  # mono
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * 2))
    buf.write(struct.pack("<H", 2))
    buf.write(struct.pack("<H", 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(b"\x00" * data_size)
    atomic_write_wav(path, buf.getvalue())


def concat_wav_files(
    files: list[Path],
    output: Path,
    output_format: str = "wav",
    *,
    process_runner: ThreadProcessRunner | None = None,
) -> None:
    """ffmpeg concat demuxer で複数 WAV ファイルを結合.

    Args:
        files: 結合する WAV ファイルパスのリスト
        output: 出力先パス
        output_format: 出力フォーマット (wav / mp3 / flac)
    """
    if not files:
        logger.warning("No WAV files to concatenate")
        return

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        for wav in files:
            f.write(f"file '{wav.resolve()}'\n")
        concat_list = f.name

    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list]

        if output_format == "mp3":
            cmd += ["-codec:a", "libmp3lame", "-q:a", "2"]
        elif output_format == "flac":
            cmd += ["-codec:a", "flac"]
        else:
            cmd += ["-codec:a", "pcm_s16le"]

        def _run_ffmpeg(temporary_output: Path) -> None:
            # 一時名は拡張子が ``.tmp`` のため、出力formatを明示する。
            ffmpeg_cmd = [*cmd, "-f", output_format, str(temporary_output)]
            run_process = process_runner.run if process_runner else subprocess.run
            result = run_process(
                ffmpeg_cmd, capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {result.stderr}")

        if output_format == "wav":
            atomic_replace_file(
                output,
                _run_ffmpeg,
                validator=_validate_wav_file,
            )
        else:
            # MP3/FLACはWAV検証対象外だが、同じ原子的公開境界を使う。
            atomic_replace_file(output, _run_ffmpeg)
    finally:
        Path(concat_list).unlink(missing_ok=True)


def _validate_wav_file(path: Path) -> None:
    """WAV concat結果が読み取り可能か検証する."""
    try:
        with wave.open(str(path), "rb") as stream:
            stream.getparams()
    except (EOFError, OSError, wave.Error) as exc:
        raise ValueError(f"Invalid WAV output: {path}") from exc


def concat_wav_bytes(wav_parts: list[bytes]) -> bytes:
    """複数の WAV バイト列を手動で結合.

    全パートが同じフォーマット（16bit PCM）であることを前提とする.
    """
    if not wav_parts:
        return b""
    if len(wav_parts) == 1:
        return wav_parts[0]

    descriptors = [validate_audio_bytes(part, "wav") for part in wav_parts]
    first = descriptors[0]
    for descriptor in descriptors[1:]:
        if (
            descriptor.sample_rate,
            descriptor.channels,
            descriptor.sample_width,
        ) != (first.sample_rate, first.channels, first.sample_width):
            raise ValueError("WAV descriptor format mismatch (rate/channel/width)")

    combined = io.BytesIO()
    with wave.open(combined, "wb") as output:
        output.setnchannels(first.channels or 1)
        output.setsampwidth(first.sample_width or 2)
        output.setframerate(first.sample_rate or 24_000)
        for part in wav_parts:
            with wave.open(io.BytesIO(part), "rb") as stream:
                output.writeframes(stream.readframes(stream.getnframes()))
    result = combined.getvalue()
    validate_audio_bytes(result, "wav")
    return result


def get_wav_duration(wav: bytes | Path) -> float:
    """WAV ファイルまたはバイト列の長さを秒数で返す.

    wave モジュールで解析できない場合は、16bit mono 24kHz と仮定して
    フォールバック計算する.
    """
    try:
        if isinstance(wav, Path):
            with wave.open(str(wav), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate == 0:
                    return 0.0
                return frames / rate
        else:
            with wave.open(io.BytesIO(wav), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate == 0:
                    return 0.0
                return frames / rate
    except Exception:
        # Fallback: 44-byte header, 16-bit mono 24kHz
        try:
            size = len(wav) if isinstance(wav, bytes) else wav.stat().st_size
            return max(0.0, (size - 44) / (24000 * 2))
        except Exception:
            logger.warning(f"Cannot determine duration: {wav}")
            return 0.0
