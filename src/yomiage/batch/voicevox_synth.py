"""VOICEVOX batch synthesizer — WAV byte output with VoicevoxVoiceProfile support."""

from pathlib import Path
from typing import Any

from loguru import logger

from ..application.synthesis import SynthesisService
from ..core.models import VoiceIntent
from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from ..tts.audio_utils import write_silence_wav
from .manifest import SentenceEntry
from .synthesizer import BatchSynthesizer


class VoicevoxBatchSynthesizer(BatchSynthesizer):
    """VOICEVOX バッチ合成 — VoicevoxVoiceProfile統合."""

    def __init__(
        self,
        config: dict,
        param_mapper: ParamMapper,
        character_db: CharacterDB | None = None,
        voice_profile: Any = None,
        provider: Any | None = None,
        synthesis_service: SynthesisService | None = None,
    ):
        # Provider construction belongs to bootstrap.  ``provider`` is optional only so the
        # class remains import-compatible for callers that use it as a parameter builder.
        if provider is None:
            from ..bootstrap import create_tts_provider

            provider = create_tts_provider({"voicevox": config}, "voicevox")
        self.provider = provider
        self.synthesis_service = synthesis_service
        self.config = dict(config or {})
        self.param_mapper = param_mapper
        self.character_db = character_db
        self.voice_profile = voice_profile

    async def synthesize_sentence(
        self, entry: SentenceEntry, output_dir: Path
    ) -> str | None:
        filename = f"{entry.index:04d}.wav"
        output_path = output_dir / filename

        params = self._build_params(entry)

        speaker_id = params.pop("speaker_id", getattr(self.provider, "default_speaker", 47))

        try:
            intent = VoiceIntent(
                speaking_rate=float(params.get("speed", 1.0)),
                pitch=float(params.get("pitch", 0.0)),
                volume=float(params.get("volume", 0.0)),
                intonation=float(params.get("intonation", 1.0)),
                emotion=entry.emotion,
                intensity=entry.intensity,
            )
            result = await self._synthesize_request(
                text=entry.text,
                destination=output_path,
                intent=intent,
                metadata={"voice_id": str(speaker_id), **params},
            )
            if result.audio_data and not output_path.exists():
                output_path.write_bytes(result.audio_data)
            if not output_path.exists():
                raise RuntimeError("SynthesisService returned no VOICEVOX artifact")
            logger.debug(f"VOICEVOX synthesized: {filename}")
            return filename
        except Exception as e:
            logger.error(f"VOICEVOX synthesis failed for {entry.index}: {e}")
            return None

    async def generate_silence(self, duration: float, output_path: Path) -> None:
        write_silence_wav(output_path, duration)

    async def is_available(self) -> bool:
        return (await self._ensure_synthesis_service().health()).healthy

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

        # --- VoicevoxVoiceProfile path ---
        if self.voice_profile and archetype and archetype in self.voice_profile.presets:
            noise_seed = entry.speaker if entry.speaker else None
            params = self.voice_profile.compute_params(
                preset=archetype,
                emotion=entry.emotion,
                intensity=entry.intensity,
                noise_seed=noise_seed,
            )
            # Override speaker_id from character voice_id if set
            if char and char.voice_id:
                try:
                    params["speaker_id"] = int(char.voice_id)
                except (ValueError, TypeError):
                    pass
            voice_profile_handled_emotion = True

        elif entry.speaker and char and char.base_params:
            # Fallback: legacy path (no VoiceProfile or unknown archetype)
            bp = {k: v for k, v in char.base_params.items() if not k.startswith("_")}
            params.update(bp)
            if char.voice_id:
                try:
                    params["speaker_id"] = int(char.voice_id)
                except (ValueError, TypeError):
                    pass

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

        # Apply emotion via param offsets if VoiceProfile didn't handle it
        if not voice_profile_handled_emotion:
            self._apply_emotion_offsets(params, entry.emotion, entry.intensity)

        # Set default speaker_id if not set
        if "speaker_id" not in params:
            params["speaker_id"] = getattr(self.provider, "default_speaker", 47)

        if entry.tts_params:
            params.update(entry.tts_params)

        return params

    def _apply_scene_mods(self, params: dict, scene: str) -> None:
        """シーン修飾子をパラメータに適用."""
        scene_mods = self.param_mapper.scenes.get(scene, {})
        if scene_mods:
            params["speed"] = params.get("speed", 1.0) * scene_mods.get("speed", 1.0)
            params["volume"] = params.get("volume", 0.0) + scene_mods.get("volume", 0.0)

    def _apply_emotion_offsets(
        self, params: dict, emotion: str, intensity: float
    ) -> None:
        """VoiceProfile未使用時の感情パラメータオフセット適用."""
        voicevox_emotions = self.param_mapper.voicevox_emotion_styles
        if not voicevox_emotions:
            return
        emo_cfg = voicevox_emotions.get(emotion)
        if not emo_cfg:
            return

        # Apply style_id if intensity above threshold
        style_id = emo_cfg.get("style_id")
        threshold = emo_cfg.get("intensity_threshold", 0.5)
        if style_id is not None and intensity >= threshold:
            params["speaker_id"] = style_id

        # Apply param offsets scaled by intensity
        for key, offset in emo_cfg.get("param_offsets", {}).items():
            if key == "speed":
                factor = 1.0 + (offset - 1.0) * intensity
                params[key] = params.get(key, 1.0) * factor
            else:
                params[key] = params.get(key, 0.0) + offset * intensity
