"""VoiSona batch synthesizer — destination:file mode via virtiofs."""

import asyncio
import struct
from pathlib import Path

import aiohttp
from loguru import logger

from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from ..tts.voisona import POLL_INTERVAL, POLL_TIMEOUT, VoisonaProvider
from .manifest import SentenceEntry
from .synthesizer import BatchSynthesizer


class VoisonaBatchSynthesizer(BatchSynthesizer):
    """VoiSona Talk destination:file による直接WAV出力."""

    def __init__(
        self,
        config: dict,
        param_mapper: ParamMapper,
        character_db: CharacterDB | None = None,
        vm_mount: str = "Z:",
    ):
        self.provider = VoisonaProvider(config)
        self.param_mapper = param_mapper
        self.character_db = character_db
        self.vm_mount = vm_mount

    async def synthesize_sentence(
        self, entry: SentenceEntry, output_dir: Path
    ) -> str | None:
        filename = f"{entry.index:04d}.wav"
        vm_path = f"{self.vm_mount}\\{output_dir.name}\\{filename}"

        params = self._build_params(entry)

        body: dict = {
            "language": self.provider.language,
            "text": entry.text,
            "voice_name": params.get("voice_id") or self.provider.voice_name,
            "destination": "file",
            "output_file_path": vm_path,
            "force_enqueue": True,
        }

        global_params = self.provider._build_params(
            "neutral",
            params.get("speed", 1.0),
            pitch=params.get("pitch"),
            volume=params.get("volume"),
            intonation=params.get("intonation"),
            huskiness=params.get("huskiness"),
            alp=params.get("alp"),
            style_weights=params.get("style_weights"),
        )
        if global_params:
            body["global_parameters"] = global_params

        try:
            uuid = await self.provider._post_synthesis(body)
            await self._poll_file_done(uuid)
            logger.debug(f"VoiSona file synthesized: {filename}")
            return filename
        except Exception as e:
            logger.error(f"VoiSona synthesis failed for {entry.index}: {e}")
            return None

    async def _poll_file_done(self, uuid: str) -> None:
        """ファイル出力完了をポーリング."""
        timeout = aiohttp.ClientTimeout(total=POLL_TIMEOUT + 10)
        elapsed = 0.0
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while elapsed < POLL_TIMEOUT:
                async with session.get(
                    f"{self.provider._api_url}/speech-syntheses/{uuid}",
                    auth=self.provider._auth(),
                ) as resp:
                    if resp.status == 200:
                        status = await resp.json()
                        state = status.get("state")
                        if state == "succeeded":
                            return
                        if state == "failed":
                            raise RuntimeError(
                                f"VoiSona file synthesis failed: {status}"
                            )
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL

        raise RuntimeError(f"VoiSona file synthesis timed out after {POLL_TIMEOUT}s")

    async def generate_silence(self, duration: float, output_path: Path) -> None:
        write_silence_wav(output_path, duration)

    async def is_available(self) -> bool:
        return await self.provider.is_available()

    def _build_params(self, entry: SentenceEntry) -> dict:
        """SentenceEntryからTTSパラメータを構築."""
        params: dict = {}

        # キャラクターベースパラメータ
        if entry.speaker and self.character_db:
            char = self.character_db.characters.get(entry.speaker)
            if char and char.base_params:
                params.update(char.base_params)
                if char.voice_id:
                    params["voice_id"] = char.voice_id
        elif entry.viewpoint_character and self.character_db:
            char = self.character_db.characters.get(entry.viewpoint_character)
            if char and char.base_params:
                # 地の文は控えめにキャラパラメータを適用
                for k in ("pitch", "huskiness", "alp"):
                    if k in char.base_params:
                        params[k] = char.base_params[k] * 0.3

        # シーン・感情パラメータ
        scene_mods = self.param_mapper.scenes.get(entry.scene, {})
        if scene_mods:
            params["speed"] = params.get("speed", 1.0) * scene_mods.get("speed", 1.0)
            params["volume"] = params.get("volume", 0.0) + scene_mods.get("volume", 0.0)

        emotion_style = self.param_mapper.emotion_styles.get(entry.emotion)
        if emotion_style:
            intensity = entry.intensity
            if intensity < 1.0:
                neutral = self.param_mapper.emotion_styles.get(
                    "neutral", [1.0, 0.0, 0.0, 0.0, 0.0]
                )
                emotion_style = [
                    n * (1 - intensity) + s * intensity
                    for n, s in zip(neutral, emotion_style)
                ]
            params["style_weights"] = emotion_style

        if entry.tts_params:
            params.update(entry.tts_params)

        return params


def write_silence_wav(
    path: Path, duration: float, sample_rate: int = 24000
) -> None:
    """無音WAVファイルを書き出し."""
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
