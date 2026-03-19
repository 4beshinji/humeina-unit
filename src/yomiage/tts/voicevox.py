"""VOICEVOX TTS Provider — Docker-based Japanese speech synthesis."""

from pathlib import Path

import aiohttp
from loguru import logger

from .base import AudioResult, TTSProvider

DEFAULT_SPEAKERS = {
    "neutral": 47,
    "caring": 47,
    "humorous": 48,
    "alert": 46,
    "happy": 47,
}


class VoicevoxProvider(TTSProvider):
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.base_url = config.get("url", "http://localhost:50021")
        self.speakers = DEFAULT_SPEAKERS.copy()
        self.speed_scale = config.get("speed_scale", 1.0)
        self.pitch_scale = config.get("pitch_scale", 0.0)
        self.intonation_scale = config.get("intonation_scale", 1.0)
        self.default_speaker = config.get("default_speaker", 47)
        self._voices_cache: list[dict] | None = None

        speakers = config.get("speakers", {})
        if speakers:
            self.speakers.update(speakers)

    @property
    def name(self) -> str:
        return "voicevox"

    async def synthesize(
        self, text: str, voice: str = "neutral", speed: float = 1.0, **params
    ) -> AudioResult:
        # voice_id override from params takes priority
        if "voice_id" in params and params["voice_id"] is not None:
            try:
                speaker_id = int(params["voice_id"])
            except (ValueError, TypeError):
                speaker_id = self.speakers.get(voice, self.default_speaker)
        else:
            speaker_id = self.speakers.get(voice, self.default_speaker)

        effective_speed = self.speed_scale * speed
        pitch = self.pitch_scale + params.get("pitch", 0.0)
        intonation = self.intonation_scale + params.get("intonation", 0.0)

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.post(
                f"{self.base_url}/audio_query",
                params={"text": text, "speaker": speaker_id},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"VOICEVOX audio_query failed: {resp.status}")
                query = await resp.json()

            query["speedScale"] = effective_speed
            query["pitchScale"] = pitch
            query["intonationScale"] = intonation
            if "volume" in params:
                query["volumeScale"] = 1.0 + params["volume"] * 0.1

            async with session.post(
                f"{self.base_url}/synthesis",
                params={"speaker": speaker_id},
                json=query,
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"VOICEVOX synthesis failed: {resp.status}")
                audio_data = await resp.read()

        logger.debug(f"VOICEVOX synthesized: speaker={speaker_id}, speed={effective_speed}")
        return AudioResult(audio_data=audio_data, format="wav", sample_rate=24000)

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

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(f"{self.base_url}/speakers") as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def list_voices(self, use_cache: bool = True) -> list[dict]:
        if use_cache and self._voices_cache is not None:
            return self._voices_cache
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(f"{self.base_url}/speakers") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        voices = []
                        for speaker in data:
                            for style in speaker.get("styles", []):
                                voices.append(
                                    {
                                        "id": style["id"],
                                        "name": f"{speaker['name']}（{style['name']}）",
                                    }
                                )
                        self._voices_cache = voices
                        return voices
                    return []
        except Exception:
            return []
