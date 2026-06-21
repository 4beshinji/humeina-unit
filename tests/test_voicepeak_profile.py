"""Tests for VOICEPEAK voice profile system and tuner helpers."""

import tempfile
from pathlib import Path

import pytest

from yomiage.tools.voice_profile import PresetConfig
from yomiage.tools.voicepeak_profile import (
    VOICEPEAK_API_LIMITS,
    VOICEPEAK_ARCHETYPE_NAMES,
    VOICEPEAK_BASE_VALUES,
    VoicepeakEmotionConfig,
    VoicepeakVoiceProfile,
)
from yomiage.tools.voicepeak_tuner import VoicepeakTuner, _format_params

# ---------------------------------------------------------------------------
# Fixture: minimal but complete VoicepeakVoiceProfile
# ---------------------------------------------------------------------------


@pytest.fixture
def profile() -> VoicepeakVoiceProfile:
    """Build a minimal but complete VoicepeakVoiceProfile for testing."""
    return VoicepeakVoiceProfile(
        narrator_name="テスト話者",
        display_name="テスト",
        ranges={
            "speed": (70, 150),
            "pitch": (-200, 200),
        },
        presets={
            "female_young": PresetConfig(
                description="若い女性",
                params={
                    "speed": 100,
                    "pitch": 0,
                },
            ),
            "male_adult": PresetConfig(
                description="成人男性",
                params={
                    "speed": 85,
                    "pitch": -120,
                },
            ),
        },
        emotions={
            "neutral": VoicepeakEmotionConfig(),
            "happy": VoicepeakEmotionConfig(
                emotion_values={"happy": 80, "fun": 40},
                param_offsets={"speed": 5},
            ),
            "angry": VoicepeakEmotionConfig(
                emotion_values={"angry": 80},
                param_offsets={"pitch": -30},
            ),
            "sad": VoicepeakEmotionConfig(
                emotion_values={"sad": 80},
                param_offsets={"speed": -10},
            ),
        },
        noise={
            "speed": 2,
            "pitch": 10,
        },
    )


# ===================================================================
# VoicepeakVoiceProfile.compute_params — preset only
# ===================================================================


class TestComputeParamsPresetOnly:
    def test_known_preset_uses_preset_params(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="neutral")
        assert result["speed"] == pytest.approx(100)
        assert result["pitch"] == pytest.approx(0)

    def test_preset_overrides_base(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(preset="male_adult", emotion="neutral")
        assert result["speed"] == pytest.approx(85)
        assert result["pitch"] == pytest.approx(-120)

    def test_no_emotions_for_neutral(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="neutral")
        assert "emotions" not in result


# ===================================================================
# VoicepeakVoiceProfile.compute_params — with emotion
# ===================================================================


class TestComputeParamsWithEmotion:
    def test_happy_speed_offset_scaled_by_intensity(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=1.0
        )
        assert result["speed"] == pytest.approx(105)  # 100 + 5 * 1.0

    def test_happy_emotion_values_at_full_intensity(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=1.0
        )
        assert result["emotions"]["happy"] == 80
        assert result["emotions"]["fun"] == 40

    def test_happy_emotion_values_at_half_intensity(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=0.5
        )
        assert result["emotions"]["happy"] == 40
        assert result["emotions"]["fun"] == 20

    def test_angry_pitch_offset(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="angry", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(-30)  # 0 + (-30) * 1.0

    def test_emotion_offsets_stack_on_preset(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(
            preset="male_adult", emotion="angry", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(-150)  # -120 + (-30) * 1.0


# ===================================================================
# VoicepeakVoiceProfile.compute_params — noise
# ===================================================================


class TestComputeParamsNoise:
    def test_same_seed_same_result(self, profile: VoicepeakVoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed=42)
        r2 = profile.compute_params(preset="female_young", noise_seed=42)
        for key in ("speed", "pitch"):
            assert r1[key] == pytest.approx(r2[key])

    def test_different_seed_different_result(self, profile: VoicepeakVoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed=42)
        r2 = profile.compute_params(preset="female_young", noise_seed=99)
        diffs = [
            abs(r1[k] - r2[k]) > 1e-9
            for k in ("speed", "pitch")
        ]
        assert any(diffs)

    def test_string_seed_is_deterministic(self, profile: VoicepeakVoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed="hello")
        r2 = profile.compute_params(preset="female_young", noise_seed="hello")
        for key in ("speed", "pitch"):
            assert r1[key] == pytest.approx(r2[key])

    def test_no_noise_when_seed_is_none(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(preset="female_young", noise_seed=None)
        assert result["speed"] == pytest.approx(100)
        assert result["pitch"] == pytest.approx(0)


# ===================================================================
# VoicepeakVoiceProfile.compute_params — clamping
# ===================================================================


class TestComputeParamsClamping:
    def test_clamps_to_profile_range_upper(self, profile: VoicepeakVoiceProfile):
        profile.emotions["extreme"] = VoicepeakEmotionConfig(
            param_offsets={"pitch": 500},
        )
        result = profile.compute_params(
            preset="female_young", emotion="extreme", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(200)  # profile range max

    def test_clamps_to_profile_range_lower(self, profile: VoicepeakVoiceProfile):
        profile.emotions["extreme_low"] = VoicepeakEmotionConfig(
            param_offsets={"pitch": -500},
        )
        result = profile.compute_params(
            preset="female_young", emotion="extreme_low", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(-200)  # profile range min

    def test_api_limits_override_wider_range(self):
        wide_profile = VoicepeakVoiceProfile(
            narrator_name="wide",
            display_name="wide",
            ranges={"pitch": (-9999, 9999)},
            presets={
                "test": PresetConfig(description="test", params={"pitch": 500}),
            },
        )
        result = wide_profile.compute_params(preset="test")
        assert result["pitch"] == pytest.approx(300)  # API limit

    def test_speed_clamped(self, profile: VoicepeakVoiceProfile):
        profile.emotions["fast"] = VoicepeakEmotionConfig(
            param_offsets={"speed": 100},
        )
        result = profile.compute_params(
            preset="female_young", emotion="fast", intensity=1.0
        )
        assert result["speed"] == pytest.approx(150)  # profile range max


# ===================================================================
# Unknown preset/emotion
# ===================================================================


class TestComputeParamsUnknown:
    def test_unknown_preset_uses_base(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(preset="nonexistent_preset")
        for key in VOICEPEAK_BASE_VALUES:
            assert result[key] == pytest.approx(VOICEPEAK_BASE_VALUES[key])

    def test_unknown_emotion_no_offsets(self, profile: VoicepeakVoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="nonexistent")
        assert result["speed"] == pytest.approx(100)
        assert result["pitch"] == pytest.approx(0)
        assert "emotions" not in result


# ===================================================================
# suggest_preset_params
# ===================================================================


class TestSuggestPresetParams:
    def test_positive_hint(self, profile: VoicepeakVoiceProfile):
        # female_child: pitch hint = 0.55
        # base=0, hint=0.55, range=(-200, 200) → 0 + 0.55 * (200 - 0) = 110
        params = profile.suggest_preset_params("female_child")
        assert params["pitch"] == pytest.approx(110, abs=1)

    def test_negative_hint(self, profile: VoicepeakVoiceProfile):
        # male_adult: pitch hint = -0.6
        # base=0, hint=-0.6, range=(-200, 200) → 0 + (-0.6) * (0 - (-200)) = -120
        params = profile.suggest_preset_params("male_adult")
        assert params["pitch"] == pytest.approx(-120, abs=1)

    def test_zero_hint_returns_base(self, profile: VoicepeakVoiceProfile):
        params = profile.suggest_preset_params("female_young")
        assert params["pitch"] == pytest.approx(0, abs=1)
        assert params["speed"] == pytest.approx(100, abs=1)

    def test_result_keys(self, profile: VoicepeakVoiceProfile):
        params = profile.suggest_preset_params("male_adult")
        assert set(params.keys()) == {"speed", "pitch"}


# ===================================================================
# YAML persistence
# ===================================================================


class TestYamlRoundTrip:
    def test_round_trip(self, profile: VoicepeakVoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_profile.yaml"
            profile.save(path)
            loaded = VoicepeakVoiceProfile.load(path)

            assert loaded.narrator_name == profile.narrator_name
            assert loaded.display_name == profile.display_name

            for key in profile.ranges:
                lo_orig, hi_orig = profile.ranges[key]
                lo_load, hi_load = loaded.ranges[key]
                assert lo_load == pytest.approx(lo_orig)
                assert hi_load == pytest.approx(hi_orig)

            assert set(loaded.presets.keys()) == set(profile.presets.keys())
            for name in profile.presets:
                for pkey, pval in profile.presets[name].params.items():
                    assert loaded.presets[name].params[pkey] == pytest.approx(pval)
                assert loaded.presets[name].description == profile.presets[name].description

            assert set(loaded.emotions.keys()) == set(profile.emotions.keys())
            for name in profile.emotions:
                assert loaded.emotions[name].emotion_values == profile.emotions[name].emotion_values
                for okey in profile.emotions[name].param_offsets:
                    assert loaded.emotions[name].param_offsets[okey] == pytest.approx(
                        profile.emotions[name].param_offsets[okey]
                    )

            for key in profile.noise:
                assert loaded.noise[key] == pytest.approx(profile.noise[key])

    def test_round_trip_compute_params_match(self, profile: VoicepeakVoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_profile.yaml"
            profile.save(path)
            loaded = VoicepeakVoiceProfile.load(path)

            for preset in ("female_young", "male_adult"):
                for emotion in ("neutral", "happy", "angry", "sad"):
                    r_orig = profile.compute_params(preset, emotion, intensity=0.7)
                    r_load = loaded.compute_params(preset, emotion, intensity=0.7)
                    for key in ("speed", "pitch"):
                        assert r_load[key] == pytest.approx(r_orig[key])


# ===================================================================
# create_default / find
# ===================================================================


class TestCreateDefault:
    def test_creates_all_nine_presets(self):
        profile = VoicepeakVoiceProfile.create_default()
        assert len(profile.presets) == 9
        for name in VOICEPEAK_ARCHETYPE_NAMES:
            assert name in profile.presets

    def test_has_ranges(self):
        profile = VoicepeakVoiceProfile.create_default()
        for key in VOICEPEAK_API_LIMITS:
            assert key in profile.ranges
            assert profile.ranges[key] == VOICEPEAK_API_LIMITS[key]

    def test_has_noise(self):
        profile = VoicepeakVoiceProfile.create_default()
        for key in ("speed", "pitch"):
            assert key in profile.noise

    def test_has_emotions(self):
        profile = VoicepeakVoiceProfile.create_default()
        assert "neutral" in profile.emotions
        assert "happy" in profile.emotions
        assert "angry" in profile.emotions


class TestFind:
    def test_find_existing_profile(self, profile: VoicepeakVoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            search_dir = Path(tmpdir)
            profile.save(search_dir / "test_profile.yaml")

            found = VoicepeakVoiceProfile.find("テスト話者", search_dirs=[search_dir])
            assert found is not None
            assert found.narrator_name == "テスト話者"

    def test_find_returns_none_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            found = VoicepeakVoiceProfile.find("nonexistent", search_dirs=[Path(tmpdir)])
            assert found is None

    def test_find_returns_none_for_missing_directory(self):
        found = VoicepeakVoiceProfile.find("anything", search_dirs=[Path("/nonexistent/dir")])
        assert found is None


# ===================================================================
# Tuner helpers
# ===================================================================


class TestFormatParams:
    def test_basic_formatting(self):
        params = {
            "speed": 110,
            "pitch": 50,
            "emotions": {"happy": 80, "fun": 40},
        }
        result = _format_params(params)
        assert "speed=110" in result
        assert "pitch=50" in result
        assert "happy=80" in result

    def test_empty_params(self):
        assert _format_params({}) == ""


class TestParseAdjustments:
    @pytest.fixture
    def tuner(self, profile: VoicepeakVoiceProfile) -> VoicepeakTuner:
        return VoicepeakTuner(profile=profile, voicepeak_path="/nonexistent/voicepeak")

    def test_basic_parsing(self, tuner: VoicepeakTuner):
        current = {"speed": 100, "pitch": 0}
        result = tuner._parse_adjustments("speed 110 pitch 50", current)
        assert result["speed"] == pytest.approx(110)
        assert result["pitch"] == pytest.approx(50)

    def test_preserves_unmentioned_keys(self, tuner: VoicepeakTuner):
        current = {"speed": 100, "pitch": 0}
        result = tuner._parse_adjustments("speed 110", current)
        assert result["speed"] == pytest.approx(110)
        assert result["pitch"] == pytest.approx(0)

    def test_unknown_key_ignored(self, tuner: VoicepeakTuner):
        current = {"speed": 100}
        result = tuner._parse_adjustments("foobar 123", current)
        assert "foobar" not in result
        assert result["speed"] == pytest.approx(100)

    def test_empty_string(self, tuner: VoicepeakTuner):
        current = {"speed": 110}
        result = tuner._parse_adjustments("", current)
        assert result["speed"] == pytest.approx(110)


class TestParseEmotions:
    @pytest.fixture
    def tuner(self, profile: VoicepeakVoiceProfile) -> VoicepeakTuner:
        return VoicepeakTuner(profile=profile, voicepeak_path="/nonexistent/voicepeak")

    def test_basic_parsing(self, tuner: VoicepeakTuner):
        current = {}
        result = tuner._parse_emotions("happy=80 fun=40", current)
        assert result["happy"] == 80
        assert result["fun"] == 40

    def test_clamps_values(self, tuner: VoicepeakTuner):
        result = tuner._parse_emotions("happy=150", {})
        assert result["happy"] == 100

    def test_ignores_unknown_axes(self, tuner: VoicepeakTuner):
        result = tuner._parse_emotions("unknown=50", {})
        assert "unknown" not in result
