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
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    pass


def write_silence_wav(
    path: Path, duration: float, sample_rate: int = 24000
) -> None:
    """無音 WAV ファイルを書き出し.

    フォーマット: PCM 16bit, mono.
    """
    num_samples = int(sample_rate * duration)
    data_size = num_samples * 2  # 16-bit mono

    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))  # PCM
        f.write(struct.pack("<H", 1))  # mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))
        f.write(struct.pack("<H", 2))
        f.write(struct.pack("<H", 16))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(b"\x00" * data_size)


def concat_wav_files(
    files: list[Path], output: Path, output_format: str = "wav"
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

        cmd.append(str(output))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr}")
    finally:
        Path(concat_list).unlink(missing_ok=True)


def concat_wav_bytes(wav_parts: list[bytes]) -> bytes:
    """複数の WAV バイト列を手動で結合.

    全パートが同じフォーマット（16bit PCM）であることを前提とする.
    """
    if not wav_parts:
        return b""
    if len(wav_parts) == 1:
        return wav_parts[0]

    first = wav_parts[0]
    if len(first) < 44 or first[:4] != b"RIFF" or first[8:12] != b"WAVE":
        raise ValueError("Invalid WAV data")

    fmt_data = first[12:]
    header_end = 12
    channels = 1
    sample_rate = 44100
    bits_per_sample = 16

    pos = 0
    data_offset = 0
    while pos < len(fmt_data) - 8:
        chunk_id = fmt_data[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", fmt_data, pos + 4)[0]
        if chunk_id == b"fmt ":
            channels = struct.unpack_from("<H", fmt_data, pos + 10)[0]
            sample_rate = struct.unpack_from("<I", fmt_data, pos + 12)[0]
            bits_per_sample = struct.unpack_from("<H", fmt_data, pos + 22)[0]
        elif chunk_id == b"data":
            data_offset = header_end + pos + 8
            break
        pos += 8 + chunk_size
        header_end += 8 + chunk_size

    if data_offset == 0:
        raise ValueError("No data chunk found")

    # Collect PCM data from all parts
    all_data = bytearray()
    for part in wav_parts:
        part_fmt = part[12:]
        part_pos = 0
        part_header_end = 12
        part_data_offset = 0
        while part_pos < len(part_fmt) - 8:
            chunk_id = part_fmt[part_pos : part_pos + 4]
            chunk_size = struct.unpack_from("<I", part_fmt, part_pos + 4)[0]
            if chunk_id == b"data":
                part_data_offset = part_header_end + part_pos + 8
                break
            part_pos += 8 + chunk_size
            part_header_end += 8 + chunk_size
        if part_data_offset == 0:
            raise ValueError("Invalid WAV part: no data chunk")
        all_data.extend(part[part_data_offset:])

    # Build output WAV header
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(all_data)

    header = bytearray()
    header.extend(b"RIFF")
    header.extend(struct.pack("<I", 36 + data_size))
    header.extend(b"WAVE")
    header.extend(b"fmt ")
    header.extend(struct.pack("<I", 16))
    header.extend(struct.pack("<H", 1))  # PCM
    header.extend(struct.pack("<H", channels))
    header.extend(struct.pack("<I", sample_rate))
    header.extend(struct.pack("<I", byte_rate))
    header.extend(struct.pack("<H", block_align))
    header.extend(struct.pack("<H", bits_per_sample))
    header.extend(b"data")
    header.extend(struct.pack("<I", data_size))

    return bytes(header) + bytes(all_data)


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
