"""VOICEVOX batch synthesizer — WAV byte output."""

from pathlib import Path

from loguru import logger

from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from ..tts.voicevox import VoicevoxProvider
from .manifest import SentenceEntry
from .synthesizer import BatchSynthesizer
from .voisona_synth import write_silence_wav


class VoicevoxBatchSynthesizer(BatchSynthesizer):
    """VOICEVOX バッチ合成 — WAVバイト書き出し."""

    def __init__(
        self,
        config: dict,
        param_mapper: ParamMapper,
        character_db: CharacterDB | None = None,
    ):
        self.provider = VoicevoxProvider(config)
        self.param_mapper = param_mapper
        self.character_db = character_db

    async def synthesize_sentence(
        self, entry: SentenceEntry, output_dir: Path
    ) -> str | None:
        filename = f"{entry.index:04d}.wav"
        output_path = output_dir / filename

        # スピーカーID 決定
        speaker_id = self.provider.default_speaker
        if entry.speaker and self.character_db:
            char = self.character_db.characters.get(entry.speaker)
            if char and char.voice_id:
                try:
                    speaker_id = int(char.voice_id)
                except (ValueError, TypeError):
                    pass

        # パラメータ
        speed = 1.0
        pitch = 0.0
        intonation = 0.0
        volume = 0.0

        scene_mods = self.param_mapper.scenes.get(entry.scene, {})
        if scene_mods:
            speed *= scene_mods.get("speed", 1.0)
            volume += scene_mods.get("volume", 0.0)

        if entry.tts_params:
            speed = entry.tts_params.get("speed", speed)
            pitch = entry.tts_params.get("pitch", pitch)

        try:
            result = await self.provider.synthesize(
                entry.text,
                voice="neutral",
                speed=speed,
                voice_id=str(speaker_id),
                pitch=pitch,
                intonation=intonation,
                volume=volume,
            )
            output_path.write_bytes(result.audio_data)
            logger.debug(f"VOICEVOX synthesized: {filename}")
            return filename
        except Exception as e:
            logger.error(f"VOICEVOX synthesis failed for {entry.index}: {e}")
            return None

    async def generate_silence(self, duration: float, output_path: Path) -> None:
        write_silence_wav(output_path, duration)

    async def is_available(self) -> bool:
        return await self.provider.is_available()
