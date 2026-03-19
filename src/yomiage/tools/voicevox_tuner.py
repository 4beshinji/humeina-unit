"""Interactive voice tuning engine for VOICEVOX.

Phases:
  1. explore_range  — sweep each parameter to find usable bounds
  2. create_preset  — tune 9 archetype presets (8 gender×age + narrator)
  3. tune_emotion   — adjust emotion configs (style_id + param_offsets)
  4. calibrate_noise — dial in per-parameter noise magnitudes
  5. demo           — play all preset × emotion combinations
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from ..tts.voicevox import VoicevoxProvider
from .voice_profile import PresetConfig
from .voicevox_profile import (
    VOICEVOX_API_LIMITS,
    VOICEVOX_ARCHETYPE_NAMES,
    VOICEVOX_BASE_VALUES,
    VOICEVOX_PARAM_KEYS,
    VoicevoxEmotionConfig,
    VoicevoxVoiceProfile,
)

DEFAULT_TEST_TEXT = "こんにちは、今日はいい天気ですね。"
DEFAULT_TUNER_DIR = Path("output/_voicevox_tuner")


class VoicevoxTuner:
    """Interactive tuning engine for VOICEVOX — synthesize, play, and collect feedback."""

    def __init__(
        self,
        profile: VoicevoxVoiceProfile,
        voicevox_url: str = "http://localhost:50021",
        test_text: str = DEFAULT_TEST_TEXT,
        output_dir: Path = DEFAULT_TUNER_DIR,
    ):
        self.profile = profile
        self.provider = VoicevoxProvider({"url": voicevox_url})
        self.test_text = test_text
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # --- Low-level synthesis + playback ---

    async def _synthesize(self, text: str, params: dict[str, Any]) -> Path:
        """Synthesize text with params → WAV file."""
        filename = f"tuner_{id(params) & 0xFFFF:04x}.wav"
        host_path = self.output_dir / filename

        speaker_id = params.get("speaker_id", self.profile.default_style_id)
        speed = params.get("speed", 1.0)
        pitch = params.get("pitch", 0.0)
        intonation = params.get("intonation", 0.0)
        volume = params.get("volume", 0.0)

        await self.provider.synthesize_to_file(
            text,
            host_path,
            speed=speed,
            voice_id=str(speaker_id),
            pitch=pitch,
            intonation=intonation,
            volume=volume,
        )
        return host_path

    def _play(self, wav_path: Path) -> None:
        """Play WAV file via aplay."""
        try:
            subprocess.run(
                ["aplay", "-q", str(wav_path)],
                check=True,
                timeout=30,
            )
        except FileNotFoundError:
            logger.warning("aplay not found, skipping playback")
        except subprocess.TimeoutExpired:
            logger.warning("Playback timed out")

    async def _synth_and_play(
        self, text: str | None = None, params: dict[str, Any] | None = None
    ) -> None:
        """Synthesize and play."""
        text = text or self.test_text
        params = params or {}
        wav_path = await self._synthesize(text, params)
        self._play(wav_path)

    def _ask(self, prompt: str, default: str = "") -> str:
        """Prompt user for input."""
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        return answer or default

    def _ask_yn(self, prompt: str, default: bool = True) -> bool:
        d = "Y/n" if default else "y/N"
        answer = self._ask(f"{prompt} [{d}]")
        if not answer:
            return default
        return answer.lower().startswith("y")

    # ================================================================
    # Phase 1: Range Exploration
    # ================================================================

    async def explore_range(
        self,
        initial_steps: int = 4,
        refine_rounds: int = 3,
        params_to_tune: list[str] | None = None,
    ) -> None:
        """Sweep each parameter to find usable boundaries."""
        params_to_tune = params_to_tune or list(VOICEVOX_PARAM_KEYS)

        print("\n=== Phase 1: Range Exploration ===")
        print(f"Test text: {self.test_text}")
        print(f"Parameters: {', '.join(params_to_tune)}")
        print()

        # Play center first
        print("Playing center (base values)...")
        await self._synth_and_play(params=dict(VOICEVOX_BASE_VALUES))
        if not self._ask_yn("Center sounds OK?"):
            print("Adjust center in config if needed. Continuing anyway.")

        for param in params_to_tune:
            api_lo, api_hi = VOICEVOX_API_LIMITS[param]
            base = VOICEVOX_BASE_VALUES[param]

            print(f"\n--- Tuning: {param} (API range: {api_lo}~{api_hi}, base: {base}) ---")

            lower_bound = await self._sweep_direction(
                param, base, api_lo, initial_steps, refine_rounds, "min"
            )
            upper_bound = await self._sweep_direction(
                param, base, api_hi, initial_steps, refine_rounds, "max"
            )

            self.profile.ranges[param] = (lower_bound, upper_bound)
            print(f"  → {param} usable range: [{lower_bound}, {upper_bound}]")

        print("\nRange exploration complete.")

    async def _sweep_direction(
        self,
        param: str,
        base: float,
        limit: float,
        steps: int,
        refine_rounds: int,
        direction: str,
    ) -> float:
        """Sweep from base toward limit, then refine boundary."""
        step_size = (limit - base) / steps
        last_ok = base
        first_bad = None

        for i in range(1, steps + 1):
            val = round(base + step_size * i, 4)

            params = dict(VOICEVOX_BASE_VALUES)
            params[param] = val
            print(f"  [{direction}] {param}={val}")
            await self._synth_and_play(params=params)

            if self._ask_yn("OK?"):
                last_ok = val
            else:
                first_bad = val
                break

        if first_bad is None:
            return round(limit, 4)

        for _ in range(refine_rounds):
            mid = round((last_ok + first_bad) / 2, 4)
            if mid == last_ok or mid == first_bad:
                break

            params = dict(VOICEVOX_BASE_VALUES)
            params[param] = mid
            print(f"  [refine] {param}={mid}")
            await self._synth_and_play(params=params)

            if self._ask_yn("OK?"):
                last_ok = mid
            else:
                first_bad = mid

        return last_ok

    # ================================================================
    # Phase 2: Preset Creation
    # ================================================================

    async def create_preset(self) -> None:
        """Tune 9 archetype presets interactively."""
        print("\n=== Phase 2: Preset Creation ===")
        print("Ranges:", {k: list(v) for k, v in self.profile.ranges.items()})
        print()

        for arch_name in VOICEVOX_ARCHETYPE_NAMES:
            desc = self.profile.presets.get(arch_name, PresetConfig(description="")).description
            suggested = self.profile.suggest_preset_params(arch_name)

            print(f"\n--- Preset: {arch_name} ({desc}) ---")
            print(f"  Suggested: {_format_params(suggested)}")

            current = dict(suggested)
            while True:
                await self._synth_and_play(params=current)
                answer = self._ask("OK? [y/n/adjust]", "y")

                if answer.lower() == "y":
                    break
                elif answer.lower() == "n":
                    print("  Skipping (keeping suggested values)")
                    break
                else:
                    current = self._parse_adjustments(answer, current)
                    print(f"  Updated: {_format_params(current)}")

            self.profile.presets[arch_name] = PresetConfig(
                description=desc or arch_name,
                params=current,
            )
            print(f"  → Saved: {_format_params(current)}")

        print("\nPreset creation complete.")

    # ================================================================
    # Phase 3: Emotion Tuning
    # ================================================================

    async def tune_emotion(self, base_preset: str = "female_young") -> None:
        """Tune emotion configs on top of a base preset."""
        print(f"\n=== Phase 3: Emotion Tuning (base: {base_preset}) ===")

        preset_cfg = self.profile.presets.get(base_preset)
        if not preset_cfg:
            print(f"Preset '{base_preset}' not found, using base values")
            base_params = dict(VOICEVOX_BASE_VALUES)
        else:
            base_params = dict(VOICEVOX_BASE_VALUES)
            base_params.update(preset_cfg.params)

        # List available styles
        print(f"\nAvailable styles: {self.profile.styles}")

        emotion_names = [e for e in self.profile.emotions if e != "neutral"]
        if not emotion_names:
            emotion_names = ["happy", "angry", "sad", "surprised", "scared", "gentle"]

        for emo_name in emotion_names:
            emo_cfg = self.profile.emotions.get(
                emo_name, VoicevoxEmotionConfig()
            )

            print(f"\n--- Emotion: {emo_name} ---")
            print(f"  style_id: {emo_cfg.style_id}")
            print(f"  param_offsets: {emo_cfg.param_offsets}")
            print(f"  intensity_threshold: {emo_cfg.intensity_threshold}")

            current_style_id = emo_cfg.style_id
            current_offsets = dict(emo_cfg.param_offsets)
            current_threshold = emo_cfg.intensity_threshold

            while True:
                # Build combined params
                combined = dict(base_params)
                for key, offset in current_offsets.items():
                    if key == "speed":
                        combined[key] = combined.get(key, 1.0) * offset
                    else:
                        combined[key] = combined.get(key, 0) + offset
                if current_style_id is not None:
                    combined["speaker_id"] = current_style_id

                await self._synth_and_play(params=combined)
                answer = self._ask("OK? [y/n/style/offsets/threshold]", "y")

                if answer.lower() == "y":
                    break
                elif answer.lower() == "n":
                    print("  Keeping current values")
                    break
                elif answer.lower().startswith("s"):
                    raw = self._ask(f"  Style ID (available: {self.profile.styles})")
                    try:
                        current_style_id = int(raw)
                    except ValueError:
                        # Try to look up by style name
                        current_style_id = self.profile.styles.get(raw, current_style_id)
                    print(f"  → style_id: {current_style_id}")
                elif answer.lower().startswith("t"):
                    raw = self._ask("  Threshold (0.0-1.0)")
                    try:
                        current_threshold = float(raw)
                    except ValueError:
                        print("  Invalid input")
                    print(f"  → threshold: {current_threshold}")
                elif answer.lower().startswith("o"):
                    raw = self._ask("  Offsets (key value ...)")
                    current_offsets = self._parse_adjustments(raw, current_offsets)
                    print(f"  → offsets: {current_offsets}")
                else:
                    current_offsets = self._parse_adjustments(answer, current_offsets)
                    print(f"  → offsets: {current_offsets}")

            self.profile.emotions[emo_name] = VoicevoxEmotionConfig(
                style_id=current_style_id,
                param_offsets=current_offsets,
                intensity_threshold=current_threshold,
            )
            print(
                f"  → Saved: style_id={current_style_id}, "
                f"offsets={current_offsets}, threshold={current_threshold}"
            )

        print("\nEmotion tuning complete.")

    # ================================================================
    # Phase 4: Noise Calibration
    # ================================================================

    async def calibrate_noise(self, base_preset: str = "female_young") -> None:
        """Play 3 variants with noise, adjust magnitude."""
        print(f"\n=== Phase 4: Noise Calibration (base: {base_preset}) ===")

        preset_cfg = self.profile.presets.get(base_preset)
        base_params = dict(VOICEVOX_BASE_VALUES)
        if preset_cfg:
            base_params.update(preset_cfg.params)

        current_noise = dict(self.profile.noise)
        print(f"  Current noise: {current_noise}")

        while True:
            print("\n  Playing 3 variants...")
            for label, seed in [("A", 1), ("B", 2), ("C", 3)]:
                rng = random.Random(seed)
                variant = dict(base_params)
                for key, magnitude in current_noise.items():
                    if key in variant:
                        offset = rng.uniform(-magnitude, magnitude)
                        if key == "speed":
                            variant[key] = variant[key] * (1.0 + offset)
                        else:
                            variant[key] = variant[key] + offset

                print(f"  [{label}] seed={seed}")
                await self._synth_and_play(params=variant)

            answer = self._ask("Differentiation? [too_similar/good/too_different]", "good")

            if answer.startswith("g"):
                break
            elif answer.startswith("too_s"):
                current_noise = {k: round(v * 1.5, 4) for k, v in current_noise.items()}
                print(f"  Noise × 1.5 → {current_noise}")
            elif answer.startswith("too_d"):
                current_noise = {k: round(v * 0.7, 4) for k, v in current_noise.items()}
                print(f"  Noise × 0.7 → {current_noise}")
            else:
                print("  Unknown input, keeping current")
                break

        self.profile.noise = current_noise
        print(f"  → Saved noise: {current_noise}")
        print("\nNoise calibration complete.")

    # ================================================================
    # Phase 5: Demo
    # ================================================================

    async def demo(self, text: str | None = None) -> None:
        """Play all preset × emotion combinations."""
        text = text or self.test_text
        print("\n=== Phase 5: Demo ===")

        for preset_name in self.profile.presets:
            for emo_name in self.profile.emotions:
                params = self.profile.compute_params(
                    preset=preset_name,
                    emotion=emo_name,
                    intensity=0.7,
                )
                label = f"{preset_name} × {emo_name}"
                print(f"  {label}")
                await self._synth_and_play(text=text, params=params)

        print("\nDemo complete.")

    # ================================================================
    # Single test
    # ================================================================

    async def test_single(
        self,
        preset: str,
        emotion: str = "neutral",
        intensity: float = 0.7,
        text: str | None = None,
        noise_seed: int | None = None,
    ) -> None:
        """Synthesize and play a single preset × emotion combination."""
        text = text or self.test_text
        params = self.profile.compute_params(
            preset=preset,
            emotion=emotion,
            intensity=intensity,
            noise_seed=noise_seed,
        )
        print(f"  {preset} × {emotion} (intensity={intensity})")
        print(f"  params: {_format_params(params)}")
        await self._synth_and_play(text=text, params=params)

    # --- Helpers ---

    def _parse_adjustments(self, raw: str, current: dict[str, float]) -> dict[str, float]:
        """Parse 'pitch 0.05 speed 0.95' → update dict."""
        tokens = raw.split()
        result = dict(current)
        i = 0
        while i < len(tokens) - 1:
            key = tokens[i]
            try:
                val = float(tokens[i + 1])
                if key in VOICEVOX_BASE_VALUES:
                    result[key] = val
                i += 2
            except ValueError:
                i += 1
        return result


def _format_params(params: dict) -> str:
    """Format params dict for display."""
    parts = []
    for key in VOICEVOX_PARAM_KEYS:
        if key in params:
            parts.append(f"{key}={params[key]}")
    if "speaker_id" in params:
        parts.append(f"sid={params['speaker_id']}")
    return ", ".join(parts)


def create_voicevox_tuner_from_config(
    config: dict,
    profile: VoicevoxVoiceProfile,
    test_text: str = DEFAULT_TEST_TEXT,
) -> VoicevoxTuner:
    """Create a VoicevoxTuner from app config dict."""
    voicevox_cfg = config.get("voicevox", {})

    return VoicevoxTuner(
        profile=profile,
        voicevox_url=voicevox_cfg.get("url", "http://localhost:50021"),
        test_text=test_text,
    )
