"""VOICEPEAK TTS Provider — CLI subprocess integration.

VOICEPEAK is an AI voice synthesis application by AH-Software/Dreamtonics.
It has no native REST API; synthesis is done via the `voicepeak` CLI command.
"""

import asyncio
import shutil
import struct
import tempfile
from pathlib import Path

from loguru import logger

from .base import AudioResult, TTSProvider

MAX_CHARS_DEFAULT = 140

# VoicePeak は同時に1プロセスしか実行できないため、プロセスレベルで排他制御する
_VOICEPEAK_LOCK = asyncio.Lock()


class VoicepeakProvider(TTSProvider):
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.voicepeak_path = config.get("path", "voicepeak")
        self.default_narrator = config.get("default_narrator", "")
        self.max_chars = config.get("max_chars", MAX_CHARS_DEFAULT)
        self.pitch_scale = config.get("pitch_scale", 300)
        self.max_retries = config.get("max_retries", 2)
        self._tmpdir: str | None = None

    @property
    def name(self) -> str:
        return "voicepeak"

    @property
    def is_slow(self) -> bool:
        return True

    def _get_tmpdir(self) -> str:
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="voicepeak_")
        return self._tmpdir

    async def synthesize(
        self, text: str, voice: str = "neutral", speed: float = 1.0, **params
    ) -> AudioResult:
        narrator = params.get("narrator") or self.default_narrator
        pitch = int(round(params.get("pitch", 0.0) * self.pitch_scale))
        pitch = max(-300, min(300, pitch))
        speed_int = int(max(50, min(200, speed * 100)))
        emotions = params.get("emotions", {})

        wav_bytes = await self._synthesize_cli(
            text, narrator=narrator, speed=speed_int, pitch=pitch, emotions=emotions
        )
        return AudioResult(audio_data=wav_bytes, format="wav", sample_rate=44100)

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        voice: str = "neutral",
        speed: float = 1.0,
        **params,
    ) -> AudioResult:
        """Synthesize and write WAV directly to file."""
        result = await self.synthesize(text, voice=voice, speed=speed, **params)
        Path(output_path).write_bytes(result.audio_data)
        return result

    async def _synthesize_cli(
        self,
        text: str,
        narrator: str = "",
        speed: int = 100,
        pitch: int = 0,
        emotions: dict[str, int] | None = None,
    ) -> bytes:
        """Run voicepeak CLI, handling text splitting and WAV concatenation.

        VoicePeak は同時に1プロセスしか実行できないため _VOICEPEAK_LOCK で排他制御する。
        失敗時は max_retries 回リトライする。
        """
        chunks = self._split_text(text, self.max_chars)
        wav_parts: list[bytes] = []

        for chunk in chunks:
            wav_parts.append(await self._synthesize_chunk(
                chunk, narrator, speed, pitch, emotions
            ))

        if len(wav_parts) == 1:
            return wav_parts[0]
        return self._concat_wav(wav_parts)

    async def _synthesize_chunk(
        self,
        chunk: str,
        narrator: str,
        speed: int,
        pitch: int,
        emotions: dict[str, int] | None,
    ) -> bytes:
        """単一チャンクを合成。ロック取得 + リトライ付き。"""
        args = self._build_cli_args(narrator, speed, pitch, emotions)
        last_err: Exception | None = None

        for attempt in range(1, self.max_retries + 2):  # max_retries+1 回試行
            async with _VOICEPEAK_LOCK:
                tmpdir = self._get_tmpdir()
                tmpfile = Path(tmpdir) / f"chunk_{id(chunk) & 0xFFFFFF:06x}.wav"
                tmpfile.unlink(missing_ok=True)

                cmd = [self.voicepeak_path, "-s", chunk, "-o", str(tmpfile)] + args
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()

                if proc.returncode == 0 and tmpfile.exists():
                    data = tmpfile.read_bytes()
                    tmpfile.unlink(missing_ok=True)
                    return data

                err_msg = stderr.decode(errors="replace").strip()
                last_err = RuntimeError(
                    f"VOICEPEAK failed (rc={proc.returncode}): {err_msg}"
                )

            if attempt <= self.max_retries:
                logger.warning(
                    f"VoicePeak attempt {attempt} failed, retrying: {chunk[:30]!r}"
                )
                await asyncio.sleep(0.5 * attempt)

        raise last_err or RuntimeError("VoicePeak synthesis failed")

    def _build_cli_args(
        self,
        narrator: str,
        speed: int,
        pitch: int,
        emotions: dict[str, int] | None,
    ) -> list[str]:
        """Build VOICEPEAK CLI flags."""
        args: list[str] = []
        if narrator:
            args.extend(["-n", narrator])
        if speed != 100:
            args.extend(["--speed", str(speed)])
        if pitch != 0:
            args.extend(["--pitch", str(pitch)])
        if emotions:
            emo_str = ",".join(f"{k}={v}" for k, v in emotions.items() if v > 0)
            if emo_str:
                args.extend(["-e", emo_str])
        return args

    @staticmethod
    def _split_text(text: str, max_chars: int) -> list[str]:
        """Split text at sentence boundaries (。！？) to stay within max_chars."""
        if len(text) <= max_chars:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= max_chars:
                chunks.append(remaining)
                break

            # Find last sentence boundary within max_chars
            best = -1
            for marker in ("。", "！", "？", "!", "?"):
                pos = remaining.rfind(marker, 0, max_chars)
                if pos > best:
                    best = pos

            if best > 0:
                chunks.append(remaining[: best + 1])
                remaining = remaining[best + 1 :]
            else:
                # No sentence boundary found — hard split
                chunks.append(remaining[:max_chars])
                remaining = remaining[max_chars:]

        return [c for c in chunks if c.strip()]

    @staticmethod
    def _concat_wav(wav_parts: list[bytes]) -> bytes:
        """Concatenate multiple WAV files (assumes same format: 16bit PCM)."""
        if not wav_parts:
            return b""

        # Parse first WAV header to get format info
        first = wav_parts[0]
        if len(first) < 44 or first[:4] != b"RIFF" or first[8:12] != b"WAVE":
            raise ValueError("Invalid WAV data")

        # Find data chunk in first file
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
            # Align to even boundary
            if chunk_size % 2:
                pos += 1

        if data_offset == 0:
            raise ValueError("No data chunk found in WAV")

        # Extract PCM data from all parts
        pcm_parts: list[bytes] = []
        for wav in wav_parts:
            offset = _find_data_offset(wav)
            data_size = struct.unpack_from("<I", wav, offset - 4)[0]
            pcm_parts.append(wav[offset : offset + data_size])

        total_pcm = b"".join(pcm_parts)
        total_data_size = len(total_pcm)

        # Build output WAV
        byte_rate = sample_rate * channels * bits_per_sample // 8
        block_align = channels * bits_per_sample // 8

        out = bytearray()
        out.extend(b"RIFF")
        out.extend(struct.pack("<I", 36 + total_data_size))
        out.extend(b"WAVE")
        out.extend(b"fmt ")
        out.extend(struct.pack("<I", 16))
        out.extend(struct.pack("<H", 1))  # PCM
        out.extend(struct.pack("<H", channels))
        out.extend(struct.pack("<I", sample_rate))
        out.extend(struct.pack("<I", byte_rate))
        out.extend(struct.pack("<H", block_align))
        out.extend(struct.pack("<H", bits_per_sample))
        out.extend(b"data")
        out.extend(struct.pack("<I", total_data_size))
        out.extend(total_pcm)

        return bytes(out)

    async def is_available(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.voicepeak_path, "--list-narrator",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    async def list_voices(self) -> list[dict]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.voicepeak_path, "--list-narrator",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []

            voices = []
            for line in stdout.decode(errors="replace").strip().splitlines():
                name = line.strip()
                if name:
                    voices.append({"id": name, "name": name})
            return voices
        except Exception:
            return []

    async def close(self) -> None:
        """Clean up temporary directory."""
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None


def _find_data_offset(wav: bytes) -> int:
    """Find the offset where PCM data begins in a WAV file."""
    pos = 12  # skip RIFF header
    while pos < len(wav) - 8:
        chunk_id = wav[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", wav, pos + 4)[0]
        if chunk_id == b"data":
            return pos + 8
        pos += 8 + chunk_size
        if chunk_size % 2:
            pos += 1
    raise ValueError("No data chunk found in WAV")
