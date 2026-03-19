"""VoicevoxVoiceProfile — per-speaker parameter ranges, presets, emotions, noise.

VOICEVOX variant of VoiceProfile. Unlike VoiSona (continuous style_weights),
VOICEVOX uses discrete speaker/style IDs for emotion expression, plus
pitch/speed/intonation/volume offsets.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .voice_profile import ARCHETYPE_DESCRIPTIONS, PresetConfig

# --- VOICEVOX API limits ---

VOICEVOX_API_LIMITS: dict[str, tuple[float, float]] = {
    "pitch": (-0.15, 0.15),
    "speed": (0.5, 2.0),
    "intonation": (0.0, 2.0),
    "volume": (-10, 10),
}

VOICEVOX_BASE_VALUES: dict[str, float] = {
    "pitch": 0.0,
    "speed": 1.0,
    "intonation": 1.0,
    "volume": 0.0,
}

VOICEVOX_PARAM_KEYS = list(VOICEVOX_BASE_VALUES.keys())

# Additive vs multiplicative (same convention as VoiSona)
_ADDITIVE_PARAMS = {"pitch", "intonation", "volume"}
_MULTIPLICATIVE_PARAMS = {"speed"}

# 9 archetype relative positions (pitch/speed only — VOICEVOX has fewer knobs)
VOICEVOX_ARCHETYPE_HINTS: dict[str, dict[str, float]] = {
    "male_child": {"pitch": 0.4, "speed": 0.15},
    "male_young": {"pitch": -0.45, "speed": -0.08},
    "male_adult": {"pitch": -0.6, "speed": -0.15},
    "male_elder": {"pitch": -0.35, "speed": -0.3},
    "female_child": {"pitch": 0.55, "speed": 0.2},
    "female_young": {"pitch": 0.0, "speed": 0.0},
    "female_adult": {"pitch": -0.1, "speed": -0.08},
    "female_elder": {"pitch": -0.2, "speed": -0.25},
    "narrator": {"pitch": 0.0, "speed": -0.08},
}

VOICEVOX_ARCHETYPE_NAMES = list(VOICEVOX_ARCHETYPE_HINTS.keys())


@dataclass
class VoicevoxEmotionConfig:
    """A single emotion configuration for VOICEVOX.

    style_id: speaker ID to switch to for this emotion (None = keep default)
    param_offsets: pitch/speed/intonation/volume offsets
    intensity_threshold: switch to style_id only above this intensity
    """

    style_id: int | None = None
    param_offsets: dict[str, float] = field(default_factory=dict)
    intensity_threshold: float = 0.5


@dataclass
class VoicevoxVoiceProfile:
    """Per-speaker profile for VOICEVOX: ranges, presets, emotions, noise."""

    speaker_name: str  # e.g. "ナースロボ_タイプT"
    display_name: str
    default_style_id: int = 47
    styles: dict[str, int] = field(default_factory=dict)
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    presets: dict[str, PresetConfig] = field(default_factory=dict)
    emotions: dict[str, VoicevoxEmotionConfig] = field(default_factory=dict)
    noise: dict[str, float] = field(default_factory=dict)

    # --- Compute ---

    def compute_params(
        self,
        preset: str,
        emotion: str = "neutral",
        intensity: float = 0.5,
        noise_seed: int | str | None = None,
    ) -> dict[str, Any]:
        """Combine preset + emotion + noise → final TTS params dict.

        Returns dict with keys: speaker_id, pitch, speed, intonation, volume.
        """
        # 1. Start from preset params (fall back to BASE_VALUES)
        preset_cfg = self.presets.get(preset)
        params: dict[str, float] = dict(VOICEVOX_BASE_VALUES)
        if preset_cfg:
            params.update(preset_cfg.params)

        # 2. Apply emotion param_offsets (scaled by intensity)
        emo_cfg = self.emotions.get(emotion)
        if emo_cfg and emo_cfg.param_offsets:
            for key, offset in emo_cfg.param_offsets.items():
                if key in _MULTIPLICATIVE_PARAMS:
                    # Interpolate multiplicative: 1.0 at intensity=0, offset at intensity=1
                    factor = 1.0 + (offset - 1.0) * intensity
                    params[key] = params.get(key, VOICEVOX_BASE_VALUES.get(key, 1.0)) * factor
                else:
                    # Additive scaled by intensity
                    params[key] = (
                        params.get(key, VOICEVOX_BASE_VALUES.get(key, 0))
                        + offset * intensity
                    )

        # 3. Determine speaker/style ID
        speaker_id = self.default_style_id
        if emo_cfg and emo_cfg.style_id is not None and intensity >= emo_cfg.intensity_threshold:
            speaker_id = emo_cfg.style_id

        # 4. Add noise
        if noise_seed is not None and self.noise:
            if isinstance(noise_seed, str):
                noise_seed = int(hashlib.md5(noise_seed.encode()).hexdigest()[:8], 16)
            rng = random.Random(noise_seed)
            for key, magnitude in self.noise.items():
                if key in params:
                    offset = rng.uniform(-magnitude, magnitude)
                    if key in _MULTIPLICATIVE_PARAMS:
                        params[key] = params[key] * (1.0 + offset)
                    else:
                        params[key] = params[key] + offset

        # 5. Clamp to usable ranges (then API limits as safety net)
        for key in VOICEVOX_PARAM_KEYS:
            if key not in params:
                continue
            lo, hi = self.ranges.get(key, VOICEVOX_API_LIMITS.get(key, (-9999, 9999)))
            api_lo, api_hi = VOICEVOX_API_LIMITS.get(key, (-9999, 9999))
            final_lo = max(lo, api_lo)
            final_hi = min(hi, api_hi)
            params[key] = max(final_lo, min(final_hi, params[key]))

        result: dict[str, Any] = dict(params)
        result["speaker_id"] = speaker_id
        return result

    def suggest_preset_params(self, archetype: str) -> dict[str, float]:
        """Suggest absolute param values for an archetype based on ranges."""
        hints = VOICEVOX_ARCHETYPE_HINTS.get(archetype, {})
        params: dict[str, float] = {}
        for key in VOICEVOX_PARAM_KEYS:
            base = VOICEVOX_BASE_VALUES[key]
            hint = hints.get(key, 0.0)
            lo, hi = self.ranges.get(key, VOICEVOX_API_LIMITS[key])
            if hint >= 0:
                params[key] = base + hint * (hi - base)
            else:
                params[key] = base + hint * (base - lo)
            params[key] = round(params[key], 3)
        return params

    # --- YAML persistence ---

    def save(self, path: Path) -> None:
        """Save profile to YAML file."""
        data: dict[str, Any] = {
            "speaker_name": self.speaker_name,
            "display_name": self.display_name,
            "default_style_id": self.default_style_id,
            "styles": dict(self.styles),
            "ranges": {k: list(v) for k, v in self.ranges.items()},
            "presets": {},
            "emotions": {},
            "noise": dict(self.noise),
        }
        for name, preset in self.presets.items():
            data["presets"][name] = {
                "description": preset.description,
                "params": dict(preset.params),
            }
        for name, emo in self.emotions.items():
            entry: dict[str, Any] = {}
            if emo.style_id is not None:
                entry["style_id"] = emo.style_id
            if emo.param_offsets:
                entry["param_offsets"] = dict(emo.param_offsets)
            entry["intensity_threshold"] = emo.intensity_threshold
            data["emotions"][name] = entry

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> VoicevoxVoiceProfile:
        """Load profile from YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        profile = cls(
            speaker_name=data.get("speaker_name", ""),
            display_name=data.get("display_name", ""),
            default_style_id=data.get("default_style_id", 47),
            styles=data.get("styles", {}),
        )

        # Ranges
        for key, val in data.get("ranges", {}).items():
            if isinstance(val, (list, tuple)) and len(val) == 2:
                profile.ranges[key] = (float(val[0]), float(val[1]))

        # Presets
        for name, pdata in data.get("presets", {}).items():
            if isinstance(pdata, dict):
                profile.presets[name] = PresetConfig(
                    description=pdata.get("description", ""),
                    params={k: float(v) for k, v in pdata.get("params", {}).items()},
                )

        # Emotions
        for name, edata in data.get("emotions", {}).items():
            if isinstance(edata, dict):
                profile.emotions[name] = VoicevoxEmotionConfig(
                    style_id=edata.get("style_id"),
                    param_offsets={
                        k: float(v) for k, v in edata.get("param_offsets", {}).items()
                    },
                    intensity_threshold=float(edata.get("intensity_threshold", 0.5)),
                )

        # Noise
        profile.noise = {k: float(v) for k, v in data.get("noise", {}).items()}

        return profile

    @classmethod
    def create_default(
        cls,
        speaker_name: str = "ナースロボ_タイプT",
        display_name: str = "ナースロボ＿タイプT",
        default_style_id: int = 47,
        styles: dict[str, int] | None = None,
    ) -> VoicevoxVoiceProfile:
        """Create a profile initialized with API limits as ranges."""
        profile = cls(
            speaker_name=speaker_name,
            display_name=display_name,
            default_style_id=default_style_id,
            styles=styles or {"normal": 47},
            ranges=dict(VOICEVOX_API_LIMITS),
            noise={
                "pitch": 0.01,
                "speed": 0.03,
                "intonation": 0.05,
            },
        )

        # Generate default presets from archetype hints
        for arch_name in VOICEVOX_ARCHETYPE_NAMES:
            profile.presets[arch_name] = PresetConfig(
                description=ARCHETYPE_DESCRIPTIONS.get(arch_name, ""),
                params=profile.suggest_preset_params(arch_name),
            )

        # Default emotions (neutral base)
        profile.emotions["neutral"] = VoicevoxEmotionConfig()

        return profile

    @classmethod
    def find(
        cls, speaker_name: str, search_dirs: list[Path] | None = None
    ) -> VoicevoxVoiceProfile | None:
        """Find and load a profile by speaker_name from standard locations."""
        if search_dirs is None:
            search_dirs = [Path("config/voicevox_profiles")]

        for d in search_dirs:
            if not d.exists():
                continue
            for yaml_path in d.glob("*.yaml"):
                try:
                    profile = cls.load(yaml_path)
                    if profile.speaker_name == speaker_name:
                        return profile
                except Exception:
                    continue
        return None
