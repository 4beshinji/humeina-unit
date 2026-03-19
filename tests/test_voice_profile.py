"""Tests for voice profile system and tuner helpers."""

import tempfile
from pathlib import Path

import pytest

from yomiage.tools.voice_profile import (
    API_LIMITS,
    BASE_VALUES,
    ARCHETYPE_NAMES,
    EmotionConfig,
    PresetConfig,
    VoiceProfile,
)
from yomiage.tools.tuner import _format_params, VoiceTuner


# ---------------------------------------------------------------------------
# Fixture: minimal but complete VoiceProfile
# ---------------------------------------------------------------------------


@pytest.fixture
def profile() -> VoiceProfile:
    """Build a minimal but complete VoiceProfile for testing."""
    return VoiceProfile(
        voice_name="test-voice_ja_JP",
        display_name="テスト",
        style_names=["Normal", "Happy", "Angry"],
        ranges={
            "pitch": (-200, 200),
            "huskiness": (-10, 10),
            "alp": (-0.5, 0.5),
            "speed": (0.5, 1.5),
            "intonation": (0.5, 1.5),
            "volume": (-3, 3),
        },
        presets={
            "female_young": PresetConfig(
                description="若い女性",
                params={
                    "pitch": 0,
                    "huskiness": 0,
                    "alp": 0,
                    "speed": 1.0,
                    "intonation": 1.0,
                },
            ),
            "male_adult": PresetConfig(
                description="成人男性",
                params={
                    "pitch": -150,
                    "huskiness": 5,
                    "alp": -0.3,
                    "speed": 0.9,
                    "intonation": 0.9,
                },
            ),
        },
        emotions={
            "neutral": EmotionConfig(
                style_weights=[1, 0, 0],
                param_offsets={},
            ),
            "happy": EmotionConfig(
                style_weights=[0.3, 0.7, 0],
                param_offsets={"pitch": 20, "speed": 1.05},
            ),
            "angry": EmotionConfig(
                style_weights=[0.2, 0, 0.8],
                param_offsets={"pitch": -30, "huskiness": 3},
            ),
        },
        noise={
            "pitch": 10,
            "huskiness": 2,
            "alp": 0.05,
            "speed": 0.03,
            "intonation": 0.05,
        },
    )


# ===================================================================
# VoiceProfile.compute_params
# ===================================================================


class TestComputeParamsPresetOnly:
    def test_known_preset_uses_preset_params(self, profile: VoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="neutral")
        assert result["pitch"] == pytest.approx(0)
        assert result["huskiness"] == pytest.approx(0)
        assert result["alp"] == pytest.approx(0)
        assert result["speed"] == pytest.approx(1.0)
        assert result["intonation"] == pytest.approx(1.0)

    def test_preset_overrides_base(self, profile: VoiceProfile):
        result = profile.compute_params(preset="male_adult", emotion="neutral")
        assert result["pitch"] == pytest.approx(-150)
        assert result["huskiness"] == pytest.approx(5)
        assert result["alp"] == pytest.approx(-0.3)
        assert result["speed"] == pytest.approx(0.9)
        assert result["intonation"] == pytest.approx(0.9)

    def test_volume_comes_from_base_when_preset_omits_it(self, profile: VoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="neutral")
        assert result["volume"] == pytest.approx(BASE_VALUES["volume"])


class TestComputeParamsWithEmotion:
    def test_happy_additive_pitch(self, profile: VoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="happy")
        assert result["pitch"] == pytest.approx(20)  # 0 + 20

    def test_happy_multiplicative_speed(self, profile: VoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="happy")
        assert result["speed"] == pytest.approx(1.05)  # 1.0 * 1.05

    def test_angry_additive_offsets(self, profile: VoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="angry")
        assert result["pitch"] == pytest.approx(-30)  # 0 + (-30)
        assert result["huskiness"] == pytest.approx(3)  # 0 + 3

    def test_emotion_offsets_stack_on_preset(self, profile: VoiceProfile):
        result = profile.compute_params(preset="male_adult", emotion="angry")
        assert result["pitch"] == pytest.approx(-180)  # -150 + (-30)
        assert result["huskiness"] == pytest.approx(8)  # 5 + 3

    def test_emotion_speed_multiplies_preset_speed(self, profile: VoiceProfile):
        result = profile.compute_params(preset="male_adult", emotion="happy")
        assert result["speed"] == pytest.approx(0.945)  # 0.9 * 1.05


class TestComputeParamsStyleWeights:
    def test_intensity_zero_returns_neutral_weights(self, profile: VoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=0.0
        )
        assert result["style_weights"] == pytest.approx([1.0, 0.0, 0.0])

    def test_intensity_one_returns_emotion_weights(self, profile: VoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=1.0
        )
        assert result["style_weights"] == pytest.approx([0.3, 0.7, 0.0])

    def test_intensity_half_interpolates(self, profile: VoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=0.5
        )
        # neutral=[1,0,0], happy=[0.3,0.7,0] → [0.65, 0.35, 0.0]
        assert result["style_weights"] == pytest.approx([0.65, 0.35, 0.0])

    def test_neutral_emotion_returns_neutral_weights(self, profile: VoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="neutral", intensity=0.5
        )
        assert result["style_weights"] == pytest.approx([1.0, 0.0, 0.0])


class TestComputeParamsNoise:
    def test_same_seed_same_result(self, profile: VoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed=42)
        r2 = profile.compute_params(preset="female_young", noise_seed=42)
        for key in ("pitch", "huskiness", "alp", "speed", "intonation"):
            assert r1[key] == pytest.approx(r2[key])

    def test_different_seed_different_result(self, profile: VoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed=42)
        r2 = profile.compute_params(preset="female_young", noise_seed=99)
        diffs = [
            abs(r1[k] - r2[k]) > 1e-9
            for k in ("pitch", "huskiness", "alp", "speed", "intonation")
        ]
        assert any(diffs)

    def test_string_seed_is_deterministic(self, profile: VoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed="hello")
        r2 = profile.compute_params(preset="female_young", noise_seed="hello")
        for key in ("pitch", "huskiness", "alp", "speed", "intonation"):
            assert r1[key] == pytest.approx(r2[key])

    def test_no_noise_when_seed_is_none(self, profile: VoiceProfile):
        result = profile.compute_params(preset="female_young", noise_seed=None)
        assert result["pitch"] == pytest.approx(0)
        assert result["speed"] == pytest.approx(1.0)


class TestComputeParamsClamping:
    def test_clamps_to_profile_range_upper(self, profile: VoiceProfile):
        profile.emotions["extreme"] = EmotionConfig(
            style_weights=[1, 0, 0],
            param_offsets={"pitch": 500},
        )
        result = profile.compute_params(preset="female_young", emotion="extreme")
        assert result["pitch"] == pytest.approx(200)

    def test_clamps_to_profile_range_lower(self, profile: VoiceProfile):
        profile.emotions["extreme_low"] = EmotionConfig(
            style_weights=[1, 0, 0],
            param_offsets={"pitch": -500},
        )
        result = profile.compute_params(preset="female_young", emotion="extreme_low")
        assert result["pitch"] == pytest.approx(-200)

    def test_api_limits_override_wider_range(self):
        wide_profile = VoiceProfile(
            voice_name="wide",
            display_name="wide",
            ranges={"pitch": (-9999, 9999)},
            presets={
                "test": PresetConfig(description="test", params={"pitch": 800}),
            },
        )
        result = wide_profile.compute_params(preset="test")
        assert result["pitch"] == pytest.approx(600)  # API limit

    def test_speed_clamped(self, profile: VoiceProfile):
        profile.emotions["fast"] = EmotionConfig(
            style_weights=[1, 0, 0],
            param_offsets={"speed": 10.0},
        )
        result = profile.compute_params(preset="female_young", emotion="fast")
        assert result["speed"] == pytest.approx(1.5)


class TestComputeParamsUnknownPreset:
    def test_unknown_preset_uses_base(self, profile: VoiceProfile):
        result = profile.compute_params(preset="nonexistent_preset")
        for key in BASE_VALUES:
            assert result[key] == pytest.approx(BASE_VALUES[key])


class TestComputeParamsUnknownEmotion:
    def test_unknown_emotion_no_offsets(self, profile: VoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="nonexistent")
        assert result["pitch"] == pytest.approx(0)
        assert result["speed"] == pytest.approx(1.0)
        assert "style_weights" not in result

    def test_unknown_emotion_same_as_base_preset(self, profile: VoiceProfile):
        result = profile.compute_params(preset="male_adult", emotion="nonexistent")
        assert result["pitch"] == pytest.approx(-150)
        assert result["huskiness"] == pytest.approx(5)


# ===================================================================
# VoiceProfile.suggest_preset_params
# ===================================================================


class TestSuggestPresetParams:
    def test_positive_hint(self, profile: VoiceProfile):
        # female_child: pitch hint = 0.55
        # base=0, hint=0.55, range=(-200, 200) → 0 + 0.55 * 200 = 110
        params = profile.suggest_preset_params("female_child")
        assert params["pitch"] == pytest.approx(110, abs=1)

    def test_negative_hint(self, profile: VoiceProfile):
        # male_adult: pitch hint = -0.6
        # base=0, hint=-0.6, range=(-200, 200) → 0 + (-0.6) * (0-(-200)) = -120
        params = profile.suggest_preset_params("male_adult")
        assert params["pitch"] == pytest.approx(-120, abs=1)

    def test_zero_hint_returns_base(self, profile: VoiceProfile):
        params = profile.suggest_preset_params("female_young")
        assert params["pitch"] == pytest.approx(BASE_VALUES["pitch"], abs=1)
        assert params["speed"] == pytest.approx(BASE_VALUES["speed"], abs=0.01)

    def test_unknown_archetype_returns_base_values(self, profile: VoiceProfile):
        params = profile.suggest_preset_params("unknown_archetype")
        assert params["pitch"] == pytest.approx(BASE_VALUES["pitch"], abs=1)
        assert params["speed"] == pytest.approx(BASE_VALUES["speed"], abs=0.01)

    def test_result_keys(self, profile: VoiceProfile):
        params = profile.suggest_preset_params("male_adult")
        assert set(params.keys()) == {"pitch", "huskiness", "alp", "speed", "intonation"}


# ===================================================================
# YAML persistence
# ===================================================================


class TestYamlRoundTrip:
    def test_round_trip(self, profile: VoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_profile.yaml"
            profile.save(path)
            loaded = VoiceProfile.load(path)

            assert loaded.voice_name == profile.voice_name
            assert loaded.display_name == profile.display_name
            assert loaded.style_names == profile.style_names

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
                assert loaded.emotions[name].style_weights == pytest.approx(
                    profile.emotions[name].style_weights
                )
                for okey in profile.emotions[name].param_offsets:
                    assert loaded.emotions[name].param_offsets[okey] == pytest.approx(
                        profile.emotions[name].param_offsets[okey]
                    )

            for key in profile.noise:
                assert loaded.noise[key] == pytest.approx(profile.noise[key])

    def test_round_trip_compute_params_match(self, profile: VoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_profile.yaml"
            profile.save(path)
            loaded = VoiceProfile.load(path)

            for preset in ("female_young", "male_adult"):
                for emotion in ("neutral", "happy", "angry"):
                    r_orig = profile.compute_params(preset, emotion, intensity=0.7)
                    r_load = loaded.compute_params(preset, emotion, intensity=0.7)
                    for key in ("pitch", "huskiness", "alp", "speed", "intonation", "volume"):
                        assert r_load[key] == pytest.approx(r_orig[key])


# ===================================================================
# VoiceProfile.create_default / find
# ===================================================================


class TestCreateDefault:
    def test_creates_all_nine_presets(self):
        profile = VoiceProfile.create_default(
            "default-test", "デフォルト",
            style_names=["Normal", "Happy", "Sad", "Angry", "Surprised"],
        )
        assert len(profile.presets) == 9
        for name in ARCHETYPE_NAMES:
            assert name in profile.presets

    def test_has_ranges(self):
        profile = VoiceProfile.create_default("default-test", "デフォルト")
        for key in API_LIMITS:
            assert key in profile.ranges
            assert profile.ranges[key] == API_LIMITS[key]

    def test_has_noise(self):
        profile = VoiceProfile.create_default("default-test", "デフォルト")
        for key in ("pitch", "huskiness", "alp", "speed", "intonation"):
            assert key in profile.noise

    def test_has_neutral_emotion(self):
        profile = VoiceProfile.create_default(
            "default-test", "デフォルト",
            style_names=["Normal", "Happy", "Sad"],
        )
        assert "neutral" in profile.emotions
        assert profile.emotions["neutral"].style_weights == [1.0, 0.0, 0.0]

    def test_neutral_weights_length_matches_style_names(self):
        style_names = ["Normal", "Happy", "Sad", "Angry", "Surprised"]
        profile = VoiceProfile.create_default("test", "test", style_names=style_names)
        assert len(profile.emotions["neutral"].style_weights) == len(style_names)

    def test_no_style_names_defaults_to_five_weights(self):
        profile = VoiceProfile.create_default("test", "test", style_names=None)
        assert len(profile.emotions["neutral"].style_weights) == 5


class TestFind:
    def test_find_existing_profile(self, profile: VoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            search_dir = Path(tmpdir)
            profile.save(search_dir / "test_profile.yaml")

            found = VoiceProfile.find("test-voice_ja_JP", search_dirs=[search_dir])
            assert found is not None
            assert found.voice_name == "test-voice_ja_JP"

    def test_find_returns_none_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            found = VoiceProfile.find("nonexistent", search_dirs=[Path(tmpdir)])
            assert found is None

    def test_find_returns_none_for_missing_directory(self):
        found = VoiceProfile.find("anything", search_dirs=[Path("/nonexistent/dir")])
        assert found is None

    def test_find_among_multiple_profiles(self, profile: VoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            search_dir = Path(tmpdir)
            profile.save(search_dir / "profile_a.yaml")

            other = VoiceProfile(voice_name="other-voice_ja_JP", display_name="その他")
            other.save(search_dir / "profile_b.yaml")

            found = VoiceProfile.find("other-voice_ja_JP", search_dirs=[search_dir])
            assert found is not None
            assert found.voice_name == "other-voice_ja_JP"


# ===================================================================
# Tuner helpers
# ===================================================================


class TestFormatParams:
    def test_basic_formatting(self):
        params = {
            "pitch": 100.0,
            "huskiness": -5.0,
            "alp": 0.15,
            "speed": 1.1,
            "intonation": 0.9,
            "volume": 2.0,
        }
        result = _format_params(params)
        assert "pitch=100" in result
        assert "huskiness=-5" in result
        assert "volume=2" in result
        assert "alp=0.15" in result
        assert "speed=1.1" in result
        assert "intonation=0.9" in result

    def test_includes_style_weights(self):
        params = {"pitch": 0, "style_weights": [0.3, 0.7, 0.0]}
        result = _format_params(params)
        assert "sw=" in result

    def test_only_known_keys_included(self):
        params = {"pitch": 50.0, "unknown_key": 999}
        result = _format_params(params)
        assert "pitch=50" in result
        assert "unknown_key" not in result

    def test_empty_params(self):
        assert _format_params({}) == ""


class TestParseAdjustments:
    @pytest.fixture
    def tuner(self, profile: VoiceProfile) -> VoiceTuner:
        return VoiceTuner(profile=profile, voisona_url="http://localhost:9999")

    def test_basic_parsing(self, tuner: VoiceTuner):
        current = {"pitch": 0, "speed": 1.0}
        result = tuner._parse_adjustments("pitch -120 speed 0.95", current)
        assert result["pitch"] == pytest.approx(-120)
        assert result["speed"] == pytest.approx(0.95)

    def test_preserves_unmentioned_keys(self, tuner: VoiceTuner):
        current = {"pitch": 0, "speed": 1.0, "huskiness": 5}
        result = tuner._parse_adjustments("pitch 50", current)
        assert result["pitch"] == pytest.approx(50)
        assert result["huskiness"] == pytest.approx(5)
        assert result["speed"] == pytest.approx(1.0)

    def test_invalid_value_skipped(self, tuner: VoiceTuner):
        current = {"pitch": 0, "speed": 1.0}
        result = tuner._parse_adjustments("pitch abc speed 0.8", current)
        assert result["speed"] == pytest.approx(0.8)

    def test_unknown_key_ignored(self, tuner: VoiceTuner):
        current = {"pitch": 0}
        result = tuner._parse_adjustments("foobar 123", current)
        assert "foobar" not in result
        assert result["pitch"] == pytest.approx(0)

    def test_empty_string(self, tuner: VoiceTuner):
        current = {"pitch": 100}
        result = tuner._parse_adjustments("", current)
        assert result["pitch"] == pytest.approx(100)
