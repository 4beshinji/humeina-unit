"""VoiSona batch synthesizer — destination:file mode via virtiofs."""

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


class VoisonaBatchSynthesizer(BatchSynthesizer):
    """VoiSona Talk destination:file による直接WAV出力."""

    def __init__(
        self,
        config: dict,
        param_mapper: ParamMapper,
        character_db: CharacterDB | None = None,
        vm_mount: str = "Z:",
        voice_profile: Any = None,
        provider: Any | None = None,
        synthesis_service: SynthesisService | None = None,
    ):
        # Provider construction belongs to bootstrap; this facade only retains the legacy
        # parameter-building surface used by old Batch callers.
        if provider is None:
            from ..bootstrap import create_tts_provider

            provider = create_tts_provider({"voisona": config}, "voisona")
        self.provider = provider
        self.synthesis_service = synthesis_service
        self.config = dict(config or {})
        self.param_mapper = param_mapper
        self.character_db = character_db
        self.vm_mount = vm_mount
        self.voice_profile = voice_profile

    async def synthesize_sentence(
        self, entry: SentenceEntry, output_dir: Path
    ) -> str | None:
        filename = f"{entry.index:04d}.wav"

        params = self._build_params(entry)

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
                destination=output_dir / filename,
                intent=intent,
                metadata={
                    "run_dir": str(output_dir),
                    "vm_mount": self.vm_mount,
                    "voice_id": params.get("voice_id"),
                    **params,
                },
            )
            if result.audio_data and not (output_dir / filename).exists():
                (output_dir / filename).write_bytes(result.audio_data)
            if not (output_dir / filename).exists():
                raise RuntimeError("SynthesisService returned no VoiSona artifact")
            logger.debug(f"VoiSona file synthesized: {filename}")
            return filename
        except Exception as e:
            logger.error(f"VoiSona synthesis failed for {entry.index}: {e}")
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

        # --- VoiceProfile path: compute_params handles preset + emotion + noise ---
        if self.voice_profile and archetype and archetype in self.voice_profile.presets:
            noise_seed = entry.speaker if entry.speaker else None
            params = self.voice_profile.compute_params(
                preset=archetype,
                emotion=entry.emotion,
                intensity=entry.intensity,
                noise_seed=noise_seed,
            )
            if char and char.voice_id:
                params["voice_id"] = char.voice_id
            voice_profile_handled_emotion = True

        elif entry.speaker and char and char.base_params:
            # Fallback: legacy path (no VoiceProfile or unknown archetype)
            bp = {k: v for k, v in char.base_params.items() if not k.startswith("_")}
            params.update(bp)
            if char.voice_id:
                params["voice_id"] = char.voice_id

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
                        # Use VoiceProfile narrator preset with subdued character influence
                        vp_params = self.voice_profile.compute_params(
                            preset="narrator",
                            emotion=entry.emotion,
                            intensity=entry.intensity * 0.5,
                        )
                        char_params = self.voice_profile.compute_params(
                            preset=vp_archetype, emotion="neutral", intensity=0.0,
                        )
                        for k in ("pitch", "huskiness", "alp"):
                            if k in char_params:
                                vp_params[k] = vp_params.get(k, 0) * 0.7 + char_params[k] * 0.3
                        params = vp_params
                        voice_profile_handled_emotion = True
                    else:
                        for k in ("pitch", "huskiness", "alp"):
                            bp = vp_char.base_params
                            if k in bp and not k.startswith("_"):
                                params[k] = bp[k] * 0.3

        # Always apply scene modifiers
        self.param_mapper.apply_scene_mods(params, entry.scene)

        # Apply emotion style only when VoiceProfile didn't handle it
        if not voice_profile_handled_emotion:
            self.param_mapper.apply_emotion_style(
                params, entry.emotion, entry.intensity
            )

        if entry.tts_params:
            params.update(entry.tts_params)

        return params
