"""WAV audio playback for VOICEVOX output."""

import asyncio
import io
import wave

from loguru import logger


async def play_wav(audio_data: bytes) -> float:
    """Play WAV audio data and return duration in seconds.

    Uses aplay on Linux. Falls back to sounddevice if available.
    """
    if not audio_data:
        return 0.0

    duration = _get_wav_duration(audio_data)

    try:
        proc = await asyncio.create_subprocess_exec(
            "aplay",
            "-q",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate(input=audio_data)
        if proc.returncode != 0:
            raise RuntimeError(f"aplay exited with {proc.returncode}")
        return duration
    except FileNotFoundError:
        logger.debug("aplay not found, trying sounddevice")

    try:
        import numpy as np
        import sounddevice as sd

        with wave.open(io.BytesIO(audio_data), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            sample_width = wf.getsampwidth()
            channels = wf.getnchannels()
            rate = wf.getframerate()

        dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sample_width, np.int16)
        data = np.frombuffer(frames, dtype=dtype)
        if channels > 1:
            data = data.reshape(-1, channels)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: sd.play(data, rate) or sd.wait())
        return duration
    except ImportError:
        logger.error("No audio playback available (install sounddevice or aplay)")
        raise RuntimeError("No audio playback backend available")


def _get_wav_duration(audio_data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(audio_data), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0
