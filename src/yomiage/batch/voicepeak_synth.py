"""VOICEPEAK batch synthesizer — WAV output with VoicepeakVoiceProfile support."""

from pathlib import Path
from typing import Any

from loguru import logger

from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from ..tts.audio_utils import write_silence_wav
from ..tts.voicepeak import VoicepeakProvider
from .manifest import SentenceEntry
from .synthesizer import BatchSynthesizer


class VoicepeakBatchSynthesizer(BatchSynthesizer):
    """VOICEPEAK バッチ合成 — VoicepeakVoiceProfile統合."""

    def __init__(
        self,
        config: dict,
        param_mapper: ParamMapper,
        character_db: CharacterDB | None = None,
        voice_profile: Any = None,
    ):
        self.provider = VoicepeakProvider(config)
        self.param_mapper = param_mapper
        self.character_db = character_db
        self.voice_profile = voice_profile

    async def synthesize_sentence(
        self, entry: SentenceEntry, output_dir: Path
    ) -> str | None:
        filename = f"{entry.index:04d}.wav"
        output_path = output_dir / filename

        params = self._build_params(entry)

        speed_native = params.get("speed", 100)
        # Convert native speed (50-200) to float ratio for provider
        speed_float = speed_native / 100.0
        pitch = params.get("pitch", 0)
        emotions = params.get("emotions", {})

        try:
            result = await self.provider.synthesize(
                entry.text,
                voice="neutral",
                speed=speed_float,
                pitch=pitch / self.provider.pitch_scale if self.provider.pitch_scale else 0,
                emotions=emotions,
            )
            output_path.write_bytes(result.audio_data)
            logger.debug(f"VOICEPEAK synthesized: {filename}")
            return filename
        except Exception as e:
            logger.error(f"VOICEPEAK synthesis failed for {entry.index}: {e}")
            return None

    async def generate_silence(self, duration: float, output_path: Path) -> None:
        write_silence_wav(output_path, duration)

    async def is_available(self) -> bool:
        return await self.provider.is_available()

    def _build_params(self, entry: SentenceEntry) -> dict:
        """SentenceEntryからTTSパラメータを構築."""
        params: dict = {}
        voice_profile_handled_emotion = False

        # Determine archetype and character
        archetype = None
        char = None
        if entry.speaker and self.character_db:
            char = self.character_db.characters.get(entry.speaker)
            if char and char.base_params:
                archetype = char.base_params.get("_archetype")

        # --- VoicepeakVoiceProfile path ---
        if self.voice_profile and archetype and archetype in self.voice_profile.presets:
            noise_seed = entry.speaker if entry.speaker else None
            params = self.voice_profile.compute_params(
                preset=archetype,
                emotion=entry.emotion,
                intensity=entry.intensity,
                noise_seed=noise_seed,
            )
            voice_profile_handled_emotion = True

        elif entry.speaker and char and char.base_params:
            # Fallback: legacy path (no VoiceProfile or unknown archetype)
            bp = {k: v for k, v in char.base_params.items() if not k.startswith("_")}
            params.update(bp)

        else:
            # Narration: viewpoint character with subdued params
            if entry.viewpoint_character and self.character_db:
                vp_char = self.character_db.characters.get(entry.viewpoint_character)
                if vp_char and vp_char.base_params:
                    vp_archetype = vp_char.base_params.get("_archetype")
                    has_profile = (
                        self.voice_profile
                        and vp_archetype
                        and vp_archetype in self.voice_profile.presets
                    )
                    if has_profile:
                        # Narrator preset blended with viewpoint character
                        vp_params = self.voice_profile.compute_params(
                            preset="narrator",
                            emotion=entry.emotion,
                            intensity=entry.intensity * 0.5,
                        )
                        char_params = self.voice_profile.compute_params(
                            preset=vp_archetype, emotion="neutral", intensity=0.0,
                        )
                        # Blend pitch: 70% narrator, 30% character
                        for k in ("pitch",):
                            if k in char_params:
                                vp_params[k] = vp_params.get(k, 0) * 0.7 + char_params[k] * 0.3
                        params = vp_params
                        voice_profile_handled_emotion = True
                    else:
                        for k in ("pitch",):
                            bp = vp_char.base_params
                            if k in bp and not k.startswith("_"):
                                params[k] = bp[k] * 0.3

        # Always apply scene modifiers
        self._apply_scene_mods(params, entry.scene)

        # Apply emotion via param_mapper if VoiceProfile didn't handle it
        if not voice_profile_handled_emotion:
            self._apply_emotion_offsets(params, entry.emotion, entry.intensity)

        if entry.tts_params:
            params.update(entry.tts_params)

        return params

    def _apply_scene_mods(self, params: dict, scene: str) -> None:
        """シーン修飾子をパラメータに適用."""
        scene_mods = self.param_mapper.scenes.get(scene, {})
        if scene_mods:
            params["speed"] = params.get("speed", 100) * scene_mods.get("speed", 1.0)
            params["volume"] = params.get("volume", 0.0) + scene_mods.get("volume", 0.0)

    def _apply_emotion_offsets(
        self, params: dict, emotion: str, intensity: float
    ) -> None:
        """VoiceProfile未使用時の感情パラメータオフセット適用."""
        voicepeak_emotions = self.param_mapper.voicepeak_emotion_styles
        if not voicepeak_emotions:
            return
        emo_cfg = voicepeak_emotions.get(emotion)
        if not emo_cfg:
            return

        # Apply emotion axis values
        emo_values = emo_cfg.get("emotions", {})
        if emo_values:
            emotions = params.get("emotions", {})
            for axis, value in emo_values.items():
                scaled = int(round(value * intensity))
                if scaled > 0:
                    emotions[axis] = max(0, min(100, scaled))
            if emotions:
                params["emotions"] = emotions

        # Apply param offsets scaled by intensity
        for key, offset in emo_cfg.get("param_offsets", {}).items():
            params[key] = params.get(key, 100 if key == "speed" else 0) + offset * intensity
