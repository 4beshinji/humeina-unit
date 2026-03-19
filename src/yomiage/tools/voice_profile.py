"""VoiceProfile — per-voicebank practical parameter ranges, presets, emotions, noise.

Provides a structured way to define and compute TTS parameters for VoiSona Talk,
constraining LLM-generated values to audibly pleasant ranges.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# --- API hard limits (VoiSona Talk specification) ---

API_LIMITS: dict[str, tuple[float, float]] = {
    "pitch": (-600, 600),
    "huskiness": (-20, 20),
    "alp": (-1.0, 1.0),
    "speed": (0.2, 5.0),
    "intonation": (0.0, 2.0),
    "volume": (-8, 8),
}

BASE_VALUES: dict[str, float] = {
    "pitch": 0,
    "huskiness": 0,
    "alp": 0.0,
    "speed": 1.0,
    "intonation": 1.0,
    "volume": 0,
}

# Parameter keys that use additive offsets
_ADDITIVE_PARAMS = {"pitch", "huskiness", "alp", "volume", "intonation"}
# Parameter keys that use multiplicative offsets
_MULTIPLICATIVE_PARAMS = {"speed"}

ALL_PARAM_KEYS = list(BASE_VALUES.keys())

# 9 archetype relative positions within the usable range (-1.0 to 1.0).
# 0.0 = base value, positive = toward max, negative = toward min.
ARCHETYPE_HINTS: dict[str, dict[str, float]] = {
    "male_child": {"pitch": 0.4, "huskiness": -0.15, "alp": 0.4, "speed": 0.15},
    "male_young": {"pitch": -0.45, "huskiness": 0.15, "alp": -0.3, "speed": -0.08},
    "male_adult": {"pitch": -0.6, "huskiness": 0.3, "alp": -0.45, "speed": -0.15},
    "male_elder": {"pitch": -0.35, "huskiness": 0.5, "alp": -0.25, "speed": -0.3},
    "female_child": {"pitch": 0.55, "huskiness": -0.25, "alp": 0.5, "speed": 0.2},
    "female_young": {"pitch": 0.0, "huskiness": 0.0, "alp": 0.0, "speed": 0.0},
    "female_adult": {"pitch": -0.1, "huskiness": 0.1, "alp": -0.1, "speed": -0.08},
    "female_elder": {"pitch": -0.2, "huskiness": 0.4, "alp": -0.15, "speed": -0.25},
    "narrator": {"pitch": 0.0, "huskiness": 0.0, "alp": 0.0, "speed": -0.08},
}

ARCHETYPE_NAMES = list(ARCHETYPE_HINTS.keys())

ARCHETYPE_DESCRIPTIONS: dict[str, str] = {
    "male_child": "男児",
    "male_young": "若い男性",
    "male_adult": "成人男性",
    "male_elder": "老年男性",
    "female_child": "女児",
    "female_young": "若い女性（基本）",
    "female_adult": "成人女性",
    "female_elder": "老年女性",
    "narrator": "ナレーター（中性的）",
}


@dataclass
class PresetConfig:
    """A single archetype preset."""

    description: str
    params: dict[str, float] = field(default_factory=dict)


@dataclass
class EmotionConfig:
    """A single emotion mask."""

    style_weights: list[float] = field(default_factory=list)
    param_offsets: dict[str, float] = field(default_factory=dict)


@dataclass
class VoiceProfile:
    """Per-voicebank profile: practical ranges, presets, emotions, noise."""

    voice_name: str  # e.g. "nurse-robot-type-t_ja_JP"
    display_name: str  # e.g. "ナースロボ＿タイプT"
    style_names: list[str] = field(default_factory=list)
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    presets: dict[str, PresetConfig] = field(default_factory=dict)
    emotions: dict[str, EmotionConfig] = field(default_factory=dict)
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

        Returns dict with keys: pitch, huskiness, alp, speed, intonation, volume,
        and optionally style_weights.
        """
        # 1. Start from preset params (fall back to BASE_VALUES)
        preset_cfg = self.presets.get(preset)
        params: dict[str, float] = dict(BASE_VALUES)
        if preset_cfg:
            params.update(preset_cfg.params)

        # 2. Apply emotion param_offsets
        emo_cfg = self.emotions.get(emotion)
        if emo_cfg and emo_cfg.param_offsets:
            for key, offset in emo_cfg.param_offsets.items():
                if key in _MULTIPLICATIVE_PARAMS:
                    params[key] = params.get(key, BASE_VALUES.get(key, 1.0)) * offset
                else:
                    params[key] = params.get(key, BASE_VALUES.get(key, 0)) + offset

        # 3. Compute style_weights: interpolate between neutral and emotion by intensity
        style_weights = None
        if emo_cfg and emo_cfg.style_weights:
            neutral_cfg = self.emotions.get("neutral")
            neutral_sw = (
                neutral_cfg.style_weights
                if neutral_cfg and neutral_cfg.style_weights
                else [1.0] + [0.0] * (len(emo_cfg.style_weights) - 1)
            )
            if intensity >= 1.0:
                style_weights = list(emo_cfg.style_weights)
            else:
                style_weights = [
                    n * (1.0 - intensity) + e * intensity
                    for n, e in zip(neutral_sw, emo_cfg.style_weights)
                ]

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
        for key in ALL_PARAM_KEYS:
            if key not in params:
                continue
            lo, hi = self.ranges.get(key, API_LIMITS.get(key, (-9999, 9999)))
            api_lo, api_hi = API_LIMITS.get(key, (-9999, 9999))
            final_lo = max(lo, api_lo)
            final_hi = min(hi, api_hi)
            params[key] = max(final_lo, min(final_hi, params[key]))

        result: dict[str, Any] = dict(params)
        if style_weights is not None:
            result["style_weights"] = style_weights

        return result

    def suggest_preset_params(self, archetype: str) -> dict[str, float]:
        """Suggest absolute param values for an archetype based on ranges."""
        hints = ARCHETYPE_HINTS.get(archetype, {})
        params: dict[str, float] = {}
        for key in ("pitch", "huskiness", "alp", "speed", "intonation"):
            base = BASE_VALUES[key]
            hint = hints.get(key, 0.0)
            lo, hi = self.ranges.get(key, API_LIMITS[key])
            if hint >= 0:
                params[key] = base + hint * (hi - base)
            else:
                params[key] = base + hint * (base - lo)
            # Round for readability
            if key in ("alp",):
                params[key] = round(params[key], 2)
            elif key == "speed":
                params[key] = round(params[key], 2)
            else:
                params[key] = round(params[key])
        return params

    # --- YAML persistence ---

    def save(self, path: Path) -> None:
        """Save profile to YAML file."""
        data: dict[str, Any] = {
            "voice_name": self.voice_name,
            "display_name": self.display_name,
            "style_names": self.style_names,
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
            entry: dict[str, Any] = {"style_weights": list(emo.style_weights)}
            if emo.param_offsets:
                entry["param_offsets"] = dict(emo.param_offsets)
            data["emotions"][name] = entry

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> VoiceProfile:
        """Load profile from YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        profile = cls(
            voice_name=data.get("voice_name", ""),
            display_name=data.get("display_name", ""),
            style_names=data.get("style_names", []),
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
                profile.emotions[name] = EmotionConfig(
                    style_weights=[float(w) for w in edata.get("style_weights", [])],
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
        voice_name: str,
        display_name: str,
        style_names: list[str] | None = None,
    ) -> VoiceProfile:
        """Create a profile initialized with API limits as ranges."""
        profile = cls(
            voice_name=voice_name,
            display_name=display_name,
            style_names=style_names or [],
            ranges=dict(API_LIMITS),
            noise={
                "pitch": 15,
                "huskiness": 2,
                "alp": 0.05,
                "speed": 0.03,
                "intonation": 0.05,
            },
        )

        # Generate default presets from archetype hints
        for arch_name in ARCHETYPE_NAMES:
            profile.presets[arch_name] = PresetConfig(
                description=ARCHETYPE_DESCRIPTIONS.get(arch_name, ""),
                params=profile.suggest_preset_params(arch_name),
            )

        # Default emotions (neutral base)
        n_styles = len(style_names) if style_names else 5
        neutral_sw = [1.0] + [0.0] * (n_styles - 1)
        profile.emotions["neutral"] = EmotionConfig(style_weights=neutral_sw)

        return profile

    @classmethod
    def find(cls, voice_name: str, search_dirs: list[Path] | None = None) -> VoiceProfile | None:
        """Find and load a profile by voice_name from standard locations."""
        if search_dirs is None:
            search_dirs = [Path("config/voice_profiles")]

        # Try exact filename match first, then scan contents
        for d in search_dirs:
            if not d.exists():
                continue
            for yaml_path in d.glob("*.yaml"):
                try:
                    profile = cls.load(yaml_path)
                    if profile.voice_name == voice_name:
                        return profile
                except Exception:
                    continue
        return None
