"""VoicepeakVoiceProfile — per-narrator parameter ranges, presets, emotions, noise.

VOICEPEAK variant of VoiceProfile. Unlike VoiSona (continuous style_weights)
or VOICEVOX (discrete style IDs), VOICEPEAK uses emotion axes
(happy/fun/angry/sad, each 0-100) plus speed/pitch offsets.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .voice_profile import ARCHETYPE_DESCRIPTIONS, PresetConfig

# --- VOICEPEAK limits ---

VOICEPEAK_API_LIMITS: dict[str, tuple[float, float]] = {
    "speed": (50, 200),
    "pitch": (-300, 300),
}

VOICEPEAK_BASE_VALUES: dict[str, float] = {
    "speed": 100,
    "pitch": 0,
}

VOICEPEAK_PARAM_KEYS = list(VOICEPEAK_BASE_VALUES.keys())

VOICEPEAK_EMOTION_AXES = ["happy", "fun", "angry", "sad"]

# Additive vs multiplicative
_ADDITIVE_PARAMS = {"pitch"}
_MULTIPLICATIVE_PARAMS = {"speed"}

# 9 archetype relative positions (speed/pitch only)
VOICEPEAK_ARCHETYPE_HINTS: dict[str, dict[str, float]] = {
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

VOICEPEAK_ARCHETYPE_NAMES = list(VOICEPEAK_ARCHETYPE_HINTS.keys())

# Default emotion mappings
VOICEPEAK_DEFAULT_EMOTIONS: dict[str, dict[str, int]] = {
    "neutral": {},
    "happy": {"happy": 80, "fun": 40},
    "angry": {"angry": 80},
    "sad": {"sad": 80},
    "surprised": {"happy": 30, "fun": 60},
    "scared": {"angry": 20, "sad": 50},
    "gentle": {"happy": 40, "fun": 30},
}


@dataclass
class VoicepeakEmotionConfig:
    """A single emotion configuration for VOICEPEAK.

    emotion_values: happy/fun/angry/sad → 0-100
    param_offsets: speed/pitch offsets
    """

    emotion_values: dict[str, int] = field(default_factory=dict)
    param_offsets: dict[str, float] = field(default_factory=dict)


@dataclass
class VoicepeakVoiceProfile:
    """Per-narrator profile for VOICEPEAK: ranges, presets, emotions, noise."""

    narrator_name: str  # e.g. "Japanese Female 1"
    display_name: str
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    presets: dict[str, PresetConfig] = field(default_factory=dict)
    emotions: dict[str, VoicepeakEmotionConfig] = field(default_factory=dict)
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

        Returns dict with keys: speed, pitch, emotions (dict of axis → 0-100).
        """
        # 1. Start from preset params (fall back to BASE_VALUES)
        preset_cfg = self.presets.get(preset)
        params: dict[str, float] = dict(VOICEPEAK_BASE_VALUES)
        if preset_cfg:
            params.update(preset_cfg.params)

        # 2. Apply emotion param_offsets (scaled by intensity)
        emo_cfg = self.emotions.get(emotion)
        if emo_cfg and emo_cfg.param_offsets:
            for key, offset in emo_cfg.param_offsets.items():
                if key in _MULTIPLICATIVE_PARAMS:
                    # Additive for VOICEPEAK speed (native int 50-200)
                    base = params.get(key, VOICEPEAK_BASE_VALUES.get(key, 100))
                    params[key] = base + offset * intensity
                else:
                    params[key] = (
                        params.get(key, VOICEPEAK_BASE_VALUES.get(key, 0))
                        + offset * intensity
                    )

        # 3. Compute emotion axis values (scaled by intensity)
        emotion_values: dict[str, int] = {}
        if emo_cfg and emo_cfg.emotion_values:
            for axis, value in emo_cfg.emotion_values.items():
                scaled = int(round(value * intensity))
                if scaled > 0:
                    emotion_values[axis] = max(0, min(100, scaled))

        # 4. Add noise
        if noise_seed is not None and self.noise:
            if isinstance(noise_seed, str):
                noise_seed = int(hashlib.md5(noise_seed.encode()).hexdigest()[:8], 16)
            rng = random.Random(noise_seed)
            for key, magnitude in self.noise.items():
                if key in params:
                    offset = rng.uniform(-magnitude, magnitude)
                    params[key] = params[key] + offset

        # 5. Clamp to usable ranges (then API limits as safety net)
        for key in VOICEPEAK_PARAM_KEYS:
            if key not in params:
                continue
            lo, hi = self.ranges.get(key, VOICEPEAK_API_LIMITS.get(key, (-9999, 9999)))
            api_lo, api_hi = VOICEPEAK_API_LIMITS.get(key, (-9999, 9999))
            final_lo = max(lo, api_lo)
            final_hi = min(hi, api_hi)
            params[key] = max(final_lo, min(final_hi, params[key]))

        result: dict[str, Any] = dict(params)
        if emotion_values:
            result["emotions"] = emotion_values
        return result

    def suggest_preset_params(self, archetype: str) -> dict[str, float]:
        """Suggest absolute param values for an archetype based on ranges."""
        hints = VOICEPEAK_ARCHETYPE_HINTS.get(archetype, {})
        params: dict[str, float] = {}
        for key in VOICEPEAK_PARAM_KEYS:
            base = VOICEPEAK_BASE_VALUES[key]
            hint = hints.get(key, 0.0)
            lo, hi = self.ranges.get(key, VOICEPEAK_API_LIMITS[key])
            if hint >= 0:
                params[key] = base + hint * (hi - base)
            else:
                params[key] = base + hint * (base - lo)
            params[key] = round(params[key])
        return params

    # --- YAML persistence ---

    def save(self, path: Path) -> None:
        """Save profile to YAML file."""
        data: dict[str, Any] = {
            "narrator_name": self.narrator_name,
            "display_name": self.display_name,
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
            if emo.emotion_values:
                entry["emotion_values"] = dict(emo.emotion_values)
            if emo.param_offsets:
                entry["param_offsets"] = dict(emo.param_offsets)
            data["emotions"][name] = entry

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> VoicepeakVoiceProfile:
        """Load profile from YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        profile = cls(
            narrator_name=data.get("narrator_name", ""),
            display_name=data.get("display_name", ""),
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
                profile.emotions[name] = VoicepeakEmotionConfig(
                    emotion_values={
                        k: int(v) for k, v in edata.get("emotion_values", {}).items()
                    },
                    param_offsets={
                        k: float(v) for k, v in edata.get("param_offsets", {}).items()
                    },
                )

        # Noise
        profile.noise = {k: float(v) for k, v in data.get("noise", {}).items()}

        return profile

    @classmethod
    def create_default(
        cls,
        narrator_name: str = "",
        display_name: str = "",
    ) -> VoicepeakVoiceProfile:
        """Create a profile initialized with API limits as ranges."""
        profile = cls(
            narrator_name=narrator_name,
            display_name=display_name,
            ranges=dict(VOICEPEAK_API_LIMITS),
            noise={
                "speed": 2,
                "pitch": 10,
            },
        )

        # Generate default presets from archetype hints
        for arch_name in VOICEPEAK_ARCHETYPE_NAMES:
            profile.presets[arch_name] = PresetConfig(
                description=ARCHETYPE_DESCRIPTIONS.get(arch_name, ""),
                params=profile.suggest_preset_params(arch_name),
            )

        # Default emotions from mapping table
        for emo_name, emo_values in VOICEPEAK_DEFAULT_EMOTIONS.items():
            offsets: dict[str, float] = {}
            if emo_name == "happy":
                offsets = {"speed": 5}
            elif emo_name == "angry":
                offsets = {"speed": 10}
            elif emo_name == "sad":
                offsets = {"speed": -10}
            elif emo_name == "surprised":
                offsets = {"speed": 10}
            elif emo_name == "gentle":
                offsets = {"speed": -5}
            profile.emotions[emo_name] = VoicepeakEmotionConfig(
                emotion_values=dict(emo_values),
                param_offsets=offsets,
            )

        return profile

    @classmethod
    def find(
        cls, narrator_name: str, search_dirs: list[Path] | None = None
    ) -> VoicepeakVoiceProfile | None:
        """Find and load a profile by narrator_name from standard locations."""
        if search_dirs is None:
            search_dirs = [Path("config/voicepeak_profiles")]

        for d in search_dirs:
            if not d.exists():
                continue
            for yaml_path in d.glob("*.yaml"):
                try:
                    profile = cls.load(yaml_path)
                    if profile.narrator_name == narrator_name:
                        return profile
                except Exception:
                    continue
        return None
