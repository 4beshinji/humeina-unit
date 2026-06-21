"""Tests for VOICEVOX voice profile system and tuner helpers."""

import tempfile
from pathlib import Path

import pytest

from yomiage.tools.voice_profile import PresetConfig
from yomiage.tools.voicevox_profile import (
    VOICEVOX_API_LIMITS,
    VOICEVOX_ARCHETYPE_NAMES,
    VOICEVOX_BASE_VALUES,
    VoicevoxEmotionConfig,
    VoicevoxVoiceProfile,
)
from yomiage.tools.voicevox_tuner import VoicevoxTuner, _format_params

# ---------------------------------------------------------------------------
# Fixture: minimal but complete VoicevoxVoiceProfile
# ---------------------------------------------------------------------------


@pytest.fixture
def profile() -> VoicevoxVoiceProfile:
    """Build a minimal but complete VoicevoxVoiceProfile for testing."""
    return VoicevoxVoiceProfile(
        speaker_name="テスト話者",
        display_name="テスト",
        default_style_id=47,
        styles={"normal": 47, "fear": 48, "whisper": 49},
        ranges={
            "pitch": (-0.1, 0.1),
            "speed": (0.7, 1.5),
            "intonation": (0.5, 1.5),
            "volume": (-5, 5),
        },
        presets={
            "female_young": PresetConfig(
                description="若い女性",
                params={
                    "pitch": 0.0,
                    "speed": 1.0,
                    "intonation": 1.0,
                    "volume": 0.0,
                },
            ),
            "male_adult": PresetConfig(
                description="成人男性",
                params={
                    "pitch": -0.06,
                    "speed": 0.88,
                    "intonation": 1.0,
                    "volume": 0.0,
                },
            ),
        },
        emotions={
            "neutral": VoicevoxEmotionConfig(),
            "happy": VoicevoxEmotionConfig(
                param_offsets={"pitch": 0.02, "speed": 1.05},
            ),
            "angry": VoicevoxEmotionConfig(
                param_offsets={"pitch": -0.03, "volume": 1.0},
            ),
            "scared": VoicevoxEmotionConfig(
                style_id=48,
                param_offsets={"speed": 1.1},
                intensity_threshold=0.4,
            ),
        },
        noise={
            "pitch": 0.01,
            "speed": 0.03,
            "intonation": 0.05,
        },
    )


# ===================================================================
# VoicevoxVoiceProfile.compute_params — preset only
# ===================================================================


class TestComputeParamsPresetOnly:
    def test_known_preset_uses_preset_params(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="neutral")
        assert result["pitch"] == pytest.approx(0.0)
        assert result["speed"] == pytest.approx(1.0)
        assert result["intonation"] == pytest.approx(1.0)
        assert result["speaker_id"] == 47

    def test_preset_overrides_base(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(preset="male_adult", emotion="neutral")
        assert result["pitch"] == pytest.approx(-0.06)
        assert result["speed"] == pytest.approx(0.88)

    def test_volume_from_base_when_preset_omits_it(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="neutral")
        assert result["volume"] == pytest.approx(VOICEVOX_BASE_VALUES["volume"])

    def test_returns_speaker_id(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(preset="female_young")
        assert "speaker_id" in result
        assert result["speaker_id"] == 47


# ===================================================================
# VoicevoxVoiceProfile.compute_params — with emotion
# ===================================================================


class TestComputeParamsWithEmotion:
    def test_happy_pitch_offset_scaled_by_intensity(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(0.02)  # 0.0 + 0.02 * 1.0

    def test_happy_speed_multiplicative_scaled(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=1.0
        )
        # speed: 1.0 * (1.0 + (1.05 - 1.0) * 1.0) = 1.0 * 1.05 = 1.05
        assert result["speed"] == pytest.approx(1.05)

    def test_half_intensity_halves_offset(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="happy", intensity=0.5
        )
        assert result["pitch"] == pytest.approx(0.01)  # 0.0 + 0.02 * 0.5

    def test_angry_additive_offsets(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="angry", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(-0.03)  # 0.0 + (-0.03) * 1.0
        assert result["volume"] == pytest.approx(1.0)   # 0.0 + 1.0 * 1.0

    def test_emotion_offsets_stack_on_preset(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="male_adult", emotion="angry", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(-0.09)  # -0.06 + (-0.03) * 1.0


# ===================================================================
# VoicevoxVoiceProfile.compute_params — style_id switching
# ===================================================================


class TestComputeParamsStyleSwitch:
    def test_scared_above_threshold_switches_style(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="scared", intensity=0.5
        )
        assert result["speaker_id"] == 48  # fear style

    def test_scared_below_threshold_keeps_default(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="scared", intensity=0.3
        )
        assert result["speaker_id"] == 47  # default (below 0.4 threshold)

    def test_neutral_keeps_default_style(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(
            preset="female_young", emotion="neutral", intensity=0.8
        )
        assert result["speaker_id"] == 47


# ===================================================================
# VoicevoxVoiceProfile.compute_params — noise
# ===================================================================


class TestComputeParamsNoise:
    def test_same_seed_same_result(self, profile: VoicevoxVoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed=42)
        r2 = profile.compute_params(preset="female_young", noise_seed=42)
        for key in ("pitch", "speed", "intonation"):
            assert r1[key] == pytest.approx(r2[key])

    def test_different_seed_different_result(self, profile: VoicevoxVoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed=42)
        r2 = profile.compute_params(preset="female_young", noise_seed=99)
        diffs = [
            abs(r1[k] - r2[k]) > 1e-9
            for k in ("pitch", "speed", "intonation")
        ]
        assert any(diffs)

    def test_string_seed_is_deterministic(self, profile: VoicevoxVoiceProfile):
        r1 = profile.compute_params(preset="female_young", noise_seed="hello")
        r2 = profile.compute_params(preset="female_young", noise_seed="hello")
        for key in ("pitch", "speed", "intonation"):
            assert r1[key] == pytest.approx(r2[key])

    def test_no_noise_when_seed_is_none(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(preset="female_young", noise_seed=None)
        assert result["pitch"] == pytest.approx(0.0)
        assert result["speed"] == pytest.approx(1.0)


# ===================================================================
# VoicevoxVoiceProfile.compute_params — clamping
# ===================================================================


class TestComputeParamsClamping:
    def test_clamps_to_profile_range_upper(self, profile: VoicevoxVoiceProfile):
        profile.emotions["extreme"] = VoicevoxEmotionConfig(
            param_offsets={"pitch": 0.5},
        )
        result = profile.compute_params(
            preset="female_young", emotion="extreme", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(0.1)  # profile range max

    def test_clamps_to_profile_range_lower(self, profile: VoicevoxVoiceProfile):
        profile.emotions["extreme_low"] = VoicevoxEmotionConfig(
            param_offsets={"pitch": -0.5},
        )
        result = profile.compute_params(
            preset="female_young", emotion="extreme_low", intensity=1.0
        )
        assert result["pitch"] == pytest.approx(-0.1)  # profile range min

    def test_api_limits_override_wider_range(self):
        wide_profile = VoicevoxVoiceProfile(
            speaker_name="wide",
            display_name="wide",
            ranges={"pitch": (-9999, 9999)},
            presets={
                "test": PresetConfig(description="test", params={"pitch": 0.5}),
            },
        )
        result = wide_profile.compute_params(preset="test")
        assert result["pitch"] == pytest.approx(0.15)  # API limit

    def test_speed_clamped(self, profile: VoicevoxVoiceProfile):
        profile.emotions["fast"] = VoicevoxEmotionConfig(
            param_offsets={"speed": 10.0},
        )
        result = profile.compute_params(
            preset="female_young", emotion="fast", intensity=1.0
        )
        assert result["speed"] == pytest.approx(1.5)  # profile range max


# ===================================================================
# Unknown preset/emotion
# ===================================================================


class TestComputeParamsUnknown:
    def test_unknown_preset_uses_base(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(preset="nonexistent_preset")
        for key in VOICEVOX_BASE_VALUES:
            assert result[key] == pytest.approx(VOICEVOX_BASE_VALUES[key])

    def test_unknown_emotion_no_offsets(self, profile: VoicevoxVoiceProfile):
        result = profile.compute_params(preset="female_young", emotion="nonexistent")
        assert result["pitch"] == pytest.approx(0.0)
        assert result["speed"] == pytest.approx(1.0)
        assert result["speaker_id"] == 47


# ===================================================================
# suggest_preset_params
# ===================================================================


class TestSuggestPresetParams:
    def test_positive_hint(self, profile: VoicevoxVoiceProfile):
        # female_child: pitch hint = 0.55
        # base=0.0, hint=0.55, range=(-0.1, 0.1) → 0.0 + 0.55 * (0.1 - 0.0) = 0.055
        params = profile.suggest_preset_params("female_child")
        assert params["pitch"] == pytest.approx(0.055, abs=0.001)

    def test_negative_hint(self, profile: VoicevoxVoiceProfile):
        # male_adult: pitch hint = -0.6
        # base=0.0, hint=-0.6, range=(-0.1, 0.1) → 0.0 + (-0.6) * (0.0 - (-0.1)) = -0.06
        params = profile.suggest_preset_params("male_adult")
        assert params["pitch"] == pytest.approx(-0.06, abs=0.001)

    def test_zero_hint_returns_base(self, profile: VoicevoxVoiceProfile):
        params = profile.suggest_preset_params("female_young")
        assert params["pitch"] == pytest.approx(0.0, abs=0.001)
        assert params["speed"] == pytest.approx(1.0, abs=0.01)

    def test_result_keys(self, profile: VoicevoxVoiceProfile):
        params = profile.suggest_preset_params("male_adult")
        assert set(params.keys()) == {"pitch", "speed", "intonation", "volume"}


# ===================================================================
# YAML persistence
# ===================================================================


class TestYamlRoundTrip:
    def test_round_trip(self, profile: VoicevoxVoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_profile.yaml"
            profile.save(path)
            loaded = VoicevoxVoiceProfile.load(path)

            assert loaded.speaker_name == profile.speaker_name
            assert loaded.display_name == profile.display_name
            assert loaded.default_style_id == profile.default_style_id
            assert loaded.styles == profile.styles

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
                assert loaded.emotions[name].style_id == profile.emotions[name].style_id
                assert loaded.emotions[name].intensity_threshold == pytest.approx(
                    profile.emotions[name].intensity_threshold
                )
                for okey in profile.emotions[name].param_offsets:
                    assert loaded.emotions[name].param_offsets[okey] == pytest.approx(
                        profile.emotions[name].param_offsets[okey]
                    )

            for key in profile.noise:
                assert loaded.noise[key] == pytest.approx(profile.noise[key])

    def test_round_trip_compute_params_match(self, profile: VoicevoxVoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_profile.yaml"
            profile.save(path)
            loaded = VoicevoxVoiceProfile.load(path)

            for preset in ("female_young", "male_adult"):
                for emotion in ("neutral", "happy", "angry", "scared"):
                    r_orig = profile.compute_params(preset, emotion, intensity=0.7)
                    r_load = loaded.compute_params(preset, emotion, intensity=0.7)
                    for key in ("pitch", "speed", "intonation", "volume"):
                        assert r_load[key] == pytest.approx(r_orig[key])
                    assert r_load["speaker_id"] == r_orig["speaker_id"]


# ===================================================================
# create_default / find
# ===================================================================


class TestCreateDefault:
    def test_creates_all_nine_presets(self):
        profile = VoicevoxVoiceProfile.create_default()
        assert len(profile.presets) == 9
        for name in VOICEVOX_ARCHETYPE_NAMES:
            assert name in profile.presets

    def test_has_ranges(self):
        profile = VoicevoxVoiceProfile.create_default()
        for key in VOICEVOX_API_LIMITS:
            assert key in profile.ranges
            assert profile.ranges[key] == VOICEVOX_API_LIMITS[key]

    def test_has_noise(self):
        profile = VoicevoxVoiceProfile.create_default()
        for key in ("pitch", "speed", "intonation"):
            assert key in profile.noise

    def test_has_neutral_emotion(self):
        profile = VoicevoxVoiceProfile.create_default()
        assert "neutral" in profile.emotions


class TestFind:
    def test_find_existing_profile(self, profile: VoicevoxVoiceProfile):
        with tempfile.TemporaryDirectory() as tmpdir:
            search_dir = Path(tmpdir)
            profile.save(search_dir / "test_profile.yaml")

            found = VoicevoxVoiceProfile.find("テスト話者", search_dirs=[search_dir])
            assert found is not None
            assert found.speaker_name == "テスト話者"

    def test_find_returns_none_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            found = VoicevoxVoiceProfile.find("nonexistent", search_dirs=[Path(tmpdir)])
            assert found is None

    def test_find_returns_none_for_missing_directory(self):
        found = VoicevoxVoiceProfile.find("anything", search_dirs=[Path("/nonexistent/dir")])
        assert found is None


# ===================================================================
# Tuner helpers
# ===================================================================


class TestFormatParams:
    def test_basic_formatting(self):
        params = {
            "pitch": 0.05,
            "speed": 1.1,
            "intonation": 0.9,
            "volume": 2.0,
            "speaker_id": 47,
        }
        result = _format_params(params)
        assert "pitch=0.05" in result
        assert "speed=1.1" in result
        assert "intonation=0.9" in result
        assert "volume=2.0" in result
        assert "sid=47" in result

    def test_empty_params(self):
        assert _format_params({}) == ""


class TestParseAdjustments:
    @pytest.fixture
    def tuner(self, profile: VoicevoxVoiceProfile) -> VoicevoxTuner:
        return VoicevoxTuner(profile=profile, voicevox_url="http://localhost:9999")

    def test_basic_parsing(self, tuner: VoicevoxTuner):
        current = {"pitch": 0.0, "speed": 1.0}
        result = tuner._parse_adjustments("pitch 0.05 speed 0.95", current)
        assert result["pitch"] == pytest.approx(0.05)
        assert result["speed"] == pytest.approx(0.95)

    def test_preserves_unmentioned_keys(self, tuner: VoicevoxTuner):
        current = {"pitch": 0.0, "speed": 1.0, "intonation": 1.0}
        result = tuner._parse_adjustments("pitch 0.05", current)
        assert result["pitch"] == pytest.approx(0.05)
        assert result["intonation"] == pytest.approx(1.0)

    def test_unknown_key_ignored(self, tuner: VoicevoxTuner):
        current = {"pitch": 0.0}
        result = tuner._parse_adjustments("foobar 123", current)
        assert "foobar" not in result
        assert result["pitch"] == pytest.approx(0.0)

    def test_empty_string(self, tuner: VoicevoxTuner):
        current = {"pitch": 0.05}
        result = tuner._parse_adjustments("", current)
        assert result["pitch"] == pytest.approx(0.05)
