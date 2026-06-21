"""音声フォーマット変換ユーティリティ."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ..api.exceptions import SynthesisError

SUPPORTED_FORMATS = {"wav", "mp3", "flac", "ogg"}


def convert_audio(
    audio_data: bytes,
    output_format: str,
    *,
    sample_rate: int | None = None,
    bitrate: str | None = None,
) -> bytes:
    """音声データを ffmpeg で指定フォーマットに変換.

    Args:
        audio_data: 入力音声バイト列（WAV を推奨）
        output_format: 出力フォーマット（wav / mp3 / flac / ogg）
        sample_rate: 出力サンプリングレート（None で元のまま）
        bitrate: 出力ビットレート（例: "128k"）

    Returns:
        変換後の音声バイト列

    Raises:
        SynthesisError: ffmpeg が失敗した場合
        ValidationError: 未対応フォーマットの場合
    """
    output_format = output_format.lower()
    if output_format == "wav":
        return audio_data
    if output_format not in SUPPORTED_FORMATS:
        from ..api.exceptions import ValidationError

        raise ValidationError(
            f"Unsupported output format: {output_format}",
            details={"supported": sorted(SUPPORTED_FORMATS)},
        )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as infile:
        infile.write(audio_data)
        in_path = Path(infile.name)

    out_path = in_path.with_suffix(f".{output_format}")

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(in_path),
            "-f",
            output_format,
        ]
        if sample_rate:
            cmd += ["-ar", str(sample_rate)]
        if bitrate:
            cmd += ["-b:a", bitrate]

        if output_format == "mp3":
            cmd += ["-codec:a", "libmp3lame"]
        elif output_format == "flac":
            cmd += ["-codec:a", "flac"]
        elif output_format == "ogg":
            cmd += ["-codec:a", "libvorbis"]

        cmd.append(str(out_path))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise SynthesisError(
                f"ffmpeg conversion failed: {result.stderr}",
                details={"format": output_format},
            )

        return out_path.read_bytes()
    except SynthesisError:
        raise
    except Exception as exc:
        raise SynthesisError(
            f"Audio conversion failed: {exc}",
            details={"format": output_format},
        )
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
