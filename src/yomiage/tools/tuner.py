"""Interactive voice tuning engine for VoiSona Talk.

Phases:
  1. explore_range  — sweep each parameter to find usable bounds
  2. create_preset  — tune 9 archetype presets (8 gender×age + narrator)
  3. tune_emotion   — adjust emotion masks (style_weights + param_offsets)
  4. calibrate_noise — dial in per-parameter noise magnitudes
  5. demo           — play all preset × emotion combinations
"""

from __future__ import annotations

import asyncio
import random
import subprocess
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from ..tts.voisona import API_BASE, POLL_INTERVAL, POLL_TIMEOUT
from .voice_profile import (
    ALL_PARAM_KEYS,
    API_LIMITS,
    ARCHETYPE_NAMES,
    BASE_VALUES,
    EmotionConfig,
    PresetConfig,
    VoiceProfile,
)

DEFAULT_TEST_TEXT = "こんにちは、今日はいい天気ですね。"
DEFAULT_TUNER_DIR = Path("output/_tuner")


class VoiceTuner:
    """Interactive tuning engine — synthesize, play, and collect user feedback."""

    def __init__(
        self,
        profile: VoiceProfile,
        voisona_url: str = "http://192.168.1.173:32766",
        voisona_user: str = "",
        voisona_pass: str = "",
        vm_mount: str = "Z:",
        test_text: str = DEFAULT_TEST_TEXT,
        output_dir: Path = DEFAULT_TUNER_DIR,
    ):
        self.profile = profile
        self.api_url = f"{voisona_url}{API_BASE}"
        self.username = voisona_user
        self.password = voisona_pass
        self.vm_mount = vm_mount
        self.test_text = test_text
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self.username, self.password)

    # --- Low-level synthesis + playback ---

    async def _synthesize(self, text: str, params: dict[str, Any]) -> Path:
        """Synthesize text with params → WAV file on host."""
        filename = f"tuner_{id(params) & 0xFFFF:04x}.wav"
        host_path = self.output_dir / filename
        vm_path = f"{self.vm_mount}\\{self.output_dir.name}\\{filename}"

        body: dict[str, Any] = {
            "language": "ja_JP",
            "text": text,
            "voice_name": self.profile.voice_name,
            "destination": "file",
            "output_file_path": vm_path,
            "force_enqueue": True,
        }

        global_params: dict[str, Any] = {}
        for key in ALL_PARAM_KEYS:
            if key in params and params[key] is not None:
                global_params[key] = params[key]
        if "style_weights" in params:
            global_params["style_weights"] = params["style_weights"]

        if global_params:
            body["global_parameters"] = global_params

        # POST synthesis
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.api_url}/speech-syntheses",
                json=body,
                auth=self._auth(),
            ) as resp:
                if resp.status != 201:
                    detail = await resp.text()
                    raise RuntimeError(f"VoiSona POST failed: {resp.status} {detail}")
                result = await resp.json()
            uuid = result["uuid"]

            # Poll until done
            elapsed = 0.0
            poll_timeout = aiohttp.ClientTimeout(total=POLL_TIMEOUT + 10)
            async with aiohttp.ClientSession(timeout=poll_timeout) as poll_session:
                while elapsed < POLL_TIMEOUT:
                    async with poll_session.get(
                        f"{self.api_url}/speech-syntheses/{uuid}",
                        auth=self._auth(),
                    ) as resp2:
                        if resp2.status == 200:
                            status = await resp2.json()
                            if status.get("state") == "succeeded":
                                return host_path
                            if status.get("state") == "failed":
                                raise RuntimeError(f"Synthesis failed: {status}")
                    await asyncio.sleep(POLL_INTERVAL)
                    elapsed += POLL_INTERVAL

        raise RuntimeError(f"Synthesis timed out after {POLL_TIMEOUT}s")

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
        params_to_tune = params_to_tune or list(ALL_PARAM_KEYS)

        print("\n=== Phase 1: Range Exploration ===")
        print(f"Test text: {self.test_text}")
        print(f"Parameters: {', '.join(params_to_tune)}")
        print()

        # Play center first
        print("Playing center (base values)...")
        await self._synth_and_play(params=dict(BASE_VALUES))
        if not self._ask_yn("Center sounds OK?"):
            print("Adjust center in config if needed. Continuing anyway.")

        for param in params_to_tune:
            api_lo, api_hi = API_LIMITS[param]
            base = BASE_VALUES[param]

            print(f"\n--- Tuning: {param} (API range: {api_lo}~{api_hi}, base: {base}) ---")

            # Sweep toward minimum
            lower_bound = await self._sweep_direction(
                param, base, api_lo, initial_steps, refine_rounds, "min"
            )
            # Sweep toward maximum
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
            val = base + step_size * i
            val = round(val, 3) if isinstance(BASE_VALUES[param], float) else round(val)

            params = dict(BASE_VALUES)
            params[param] = val
            print(f"  [{direction}] {param}={val}")
            await self._synth_and_play(params=params)

            if self._ask_yn("OK?"):
                last_ok = val
            else:
                first_bad = val
                break

        if first_bad is None:
            # All steps were OK — limit is usable
            return round(limit, 3) if isinstance(BASE_VALUES[param], float) else round(limit)

        # Binary search between last_ok and first_bad
        for _ in range(refine_rounds):
            mid = (last_ok + first_bad) / 2
            if isinstance(BASE_VALUES[param], (int,)):
                mid = round(mid)
            else:
                mid = round(mid, 3)

            if mid == last_ok or mid == first_bad:
                break

            params = dict(BASE_VALUES)
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

        for arch_name in ARCHETYPE_NAMES:
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
                    # Parse adjustments: "pitch -120 speed 0.95"
                    current = self._parse_adjustments(answer, current)
                    print(f"  Updated: {_format_params(current)}")

            self.profile.presets[arch_name] = PresetConfig(
                description=desc or arch_name,
                params=current,
            )
            print(f"  → Saved: {_format_params(current)}")

        print("\nPreset creation complete.")

    # ================================================================
    # Phase 3: Emotion Mask Tuning
    # ================================================================

    async def tune_emotion(self, base_preset: str = "female_young") -> None:
        """Tune emotion masks on top of a base preset."""
        print(f"\n=== Phase 3: Emotion Mask Tuning (base: {base_preset}) ===")

        preset_cfg = self.profile.presets.get(base_preset)
        if not preset_cfg:
            print(f"Preset '{base_preset}' not found, using base values")
            base_params = dict(BASE_VALUES)
        else:
            base_params = dict(BASE_VALUES)
            base_params.update(preset_cfg.params)

        # Iterate emotions (skip neutral)
        emotion_names = [e for e in self.profile.emotions if e != "neutral"]
        if not emotion_names:
            emotion_names = ["happy", "angry", "sad", "surprised", "scared", "gentle"]

        for emo_name in emotion_names:
            emo_cfg = self.profile.emotions.get(
                emo_name,
                EmotionConfig(
                    style_weights=[1.0] + [0.0] * (len(self.profile.style_names) - 1)
                ),
            )

            print(f"\n--- Emotion: {emo_name} ---")
            print(f"  style_weights: {emo_cfg.style_weights}")
            print(f"  param_offsets: {emo_cfg.param_offsets}")

            current_sw = list(emo_cfg.style_weights)
            current_offsets = dict(emo_cfg.param_offsets)

            while True:
                # Build combined params
                combined = dict(base_params)
                for key, offset in current_offsets.items():
                    if key == "speed":
                        combined[key] = combined.get(key, 1.0) * offset
                    else:
                        combined[key] = combined.get(key, 0) + offset
                combined["style_weights"] = current_sw

                await self._synth_and_play(params=combined)
                answer = self._ask("OK? [y/n/weights/offsets]", "y")

                if answer.lower() == "y":
                    break
                elif answer.lower() == "n":
                    print("  Keeping current values")
                    break
                elif answer.lower().startswith("w"):
                    # "weights" or direct values: "0.2 0.8 0 0 0"
                    raw = self._ask("  New weights (space-separated)")
                    try:
                        current_sw = [float(x) for x in raw.split()]
                    except ValueError:
                        print("  Invalid input")
                    print(f"  → weights: {current_sw}")
                elif answer.lower().startswith("o"):
                    # "offsets" or direct: "pitch 30 speed 1.1"
                    raw = self._ask("  Offsets (key value ...)")
                    current_offsets = self._parse_offsets(raw, current_offsets)
                    print(f"  → offsets: {current_offsets}")
                else:
                    # Try as adjustment to offsets
                    current_offsets = self._parse_offsets(answer, current_offsets)
                    print(f"  → offsets: {current_offsets}")

            self.profile.emotions[emo_name] = EmotionConfig(
                style_weights=current_sw,
                param_offsets=current_offsets,
            )
            print(f"  → Saved: weights={current_sw}, offsets={current_offsets}")

        print("\nEmotion tuning complete.")

    # ================================================================
    # Phase 4: Noise Calibration
    # ================================================================

    async def calibrate_noise(self, base_preset: str = "female_young") -> None:
        """Play 3 variants with noise, adjust magnitude."""
        print(f"\n=== Phase 4: Noise Calibration (base: {base_preset}) ===")

        preset_cfg = self.profile.presets.get(base_preset)
        base_params = dict(BASE_VALUES)
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
                current_noise = {k: round(v * 1.5, 3) for k, v in current_noise.items()}
                print(f"  Noise × 1.5 → {current_noise}")
            elif answer.startswith("too_d"):
                current_noise = {k: round(v * 0.7, 3) for k, v in current_noise.items()}
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

        for preset_name, preset_cfg in self.profile.presets.items():
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
        """Parse 'pitch -120 speed 0.95' → update dict."""
        tokens = raw.split()
        result = dict(current)
        i = 0
        while i < len(tokens) - 1:
            key = tokens[i]
            try:
                val = float(tokens[i + 1])
                if key in BASE_VALUES:
                    result[key] = val
                i += 2
            except ValueError:
                i += 1
        return result

    def _parse_offsets(self, raw: str, current: dict[str, float]) -> dict[str, float]:
        """Parse offset adjustments."""
        return self._parse_adjustments(raw, current)


def _format_params(params: dict) -> str:
    """Format params dict for display."""
    parts = []
    for key in ALL_PARAM_KEYS:
        if key in params:
            v = params[key]
            if isinstance(v, float) and key not in ("speed", "alp", "intonation"):
                parts.append(f"{key}={v:.0f}")
            else:
                parts.append(f"{key}={v}")
    if "style_weights" in params:
        parts.append(f"sw={params['style_weights']}")
    return ", ".join(parts)


def create_tuner_from_config(
    config: dict,
    profile: VoiceProfile,
    test_text: str = DEFAULT_TEST_TEXT,
) -> VoiceTuner:
    """Create a VoiceTuner from app config dict."""
    voisona_cfg = config.get("voisona", {})
    batch_cfg = config.get("batch", {})

    return VoiceTuner(
        profile=profile,
        voisona_url=voisona_cfg.get("url", "http://192.168.1.173:32766"),
        voisona_user=voisona_cfg.get("username", ""),
        voisona_pass=voisona_cfg.get("password", ""),
        vm_mount=batch_cfg.get("voisona_vm_mount", "Z:"),
        test_text=test_text,
    )
