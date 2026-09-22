"""F06/F07/F16 と D01 の canonical parameter contract。"""

from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from yomiage.adapters.tts.legacy import LegacyTTSProviderAdapter
from yomiage.api.bridge import TTSBridge
from yomiage.application.synthesis import RetryPolicy, SynthesisRequest, SynthesisService
from yomiage.batch.engine import BatchEngine
from yomiage.batch.manifest import BatchManifest, SentenceEntry
from yomiage.core.models import Emotion, SegmentType, Utterance, VoiceIntent
from yomiage.core.plan import UtterancePlan
from yomiage.reader.playback import UtterancePlaybackQueue
from yomiage.studio.models import ScriptLine, SpeakerMapping
from yomiage.studio.synthesizer import StudioSynthesizer
from yomiage.tts.base import AudioResult
from yomiage.tts.voicepeak import VoicepeakProvider

from .contracts.fakes import PortFakeProvider
from .test_audio_validation import wav_bytes


def _contract_module():
    return importlib.import_module("yomiage.application.voice_parameters")


def test_f06_unset_and_explicit_neutral_are_distinct() -> None:
    contract = _contract_module()
    unset = contract.VoiceOverrides()
    neutral = contract.VoiceOverrides(
        speaking_rate=1.0,
        pitch=0.0,
        volume=0.0,
        intonation=1.0,
        emotion=Emotion.NEUTRAL,
        intensity=0.0,
    )

    assert unset.pitch is None
    assert neutral.pitch == 0.0
    assert unset != neutral


def test_f06_resolution_order_is_profile_scene_emotion_explicit_override() -> None:
    contract = _contract_module()
    resolved = contract.resolve_voice_parameters(
        VoiceIntent(emotion="angry", intensity=0.5),
        profile={"speed": 0.9, "pitch": -100, "volume": -1, "intonation": 0.8},
        scene={"speed": 1.1, "volume": 2, "intonation": 1.3},
        emotion={
            "param_offsets": {"speed": 1.2, "pitch": 20, "volume": 1, "intonation": 1.2},
            "style_weights": [0.2, 0.0, 0.8],
        },
        overrides=contract.VoiceOverrides(pitch=0.0, volume=0.0),
    )

    assert resolved.speaking_rate == pytest.approx(0.9 * 1.1 * 1.1)
    assert resolved.pitch == 0.0
    assert resolved.volume == 0.0
    assert resolved.intonation == pytest.approx(0.8 * 1.3 * 1.1)
    assert resolved.style_weights == pytest.approx((0.6, 0.0, 0.4))


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("voisona", {"speed": 1.25, "pitch": -0.5, "volume": 0.2,
                     "intonation": 1.1}),
        ("voicevox", {"speedScale": 1.25, "pitchScale": -0.5,
                      "volumeScale": 1.02, "intonationScale": 1.1}),
        ("voicepeak", {"speed": 125, "pitch": -150}),
    ],
)
def test_f07_provider_translation_happens_once_at_adapter_boundary(
    provider: str, expected: dict[str, float | int]
) -> None:
    contract = _contract_module()
    resolved = contract.ResolvedVoiceParameters(
        speaking_rate=1.25,
        pitch=-0.5,
        volume=0.2,
        intonation=1.1,
        emotion=Emotion.ANGRY,
        intensity=0.75,
    )

    translated = contract.translate_provider_parameters(provider, resolved)

    assert translated.provider == provider
    for key, value in expected.items():
        assert translated.values[key] == pytest.approx(value)


@pytest.mark.asyncio
async def test_f07_fallback_re_resolves_voice_and_native_parameters() -> None:
    class _Legacy:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def is_available(self) -> bool:
            return True

        async def list_voices(self) -> list[dict[str, str]]:
            return [{"id": f"{self.name}-voice", "name": self.name}]

        async def synthesize(
            self, _text: str, voice: str = "neutral", **kwargs: object
        ) -> AudioResult:
            self.calls.append((voice, kwargs))
            if self.fail:
                raise ConnectionError("primary unavailable")
            return AudioResult(wav_bytes())

        async def close(self) -> None:
            return None

    primary = _Legacy("voisona", fail=True)
    fallback = _Legacy("voicevox")
    service = SynthesisService(
        {
            "voisona": LegacyTTSProviderAdapter(primary),
            "voicevox": LegacyTTSProviderAdapter(fallback),
        },
        primary="voisona",
        fallback="voicevox",
        retry_policy=RetryPolicy(max_retries=0),
    )

    result = await service.synthesize(
        SynthesisRequest(
            "fallback",
            voice_intent=VoiceIntent(speaking_rate=1.25, pitch=-0.5, volume=0.2),
            metadata={"voice_id": "primary-only"},
        )
    )

    assert result.provider == "voicevox"
    assert fallback.calls[0][0] == "voicevox-voice"
    native = fallback.calls[0][1]["_native_parameters"]
    assert isinstance(native, dict)
    assert native["speedScale"] == pytest.approx(1.25)
    assert native["pitchScale"] == pytest.approx(-0.5)
    assert "voice_id" not in fallback.calls[0][1]
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "configured", "expected"),
    [
        (
            "voicevox",
            {"speed_scale": 0.8, "pitch_scale": 0.15, "intonation_scale": 0.9},
            {"speedScale": 0.8, "pitchScale": 0.15, "intonationScale": 0.9},
        ),
        ("voicepeak", {"pitch_scale": 180}, {"speed": 100, "pitch": 0}),
        (
            "voisona",
            {
                "_base_params": {
                    "speed": 0.8,
                    "pitch": 10,
                    "volume": -1,
                    "intonation": 0.9,
                    "huskiness": 2,
                    "alp": 0.1,
                },
                "_tone_overrides": {"warm": {"pitch": 40, "style_weights": [0.2, 0.8]}},
            },
            {"speed": 0.8, "pitch": 40, "volume": -1, "intonation": 0.9},
        ),
    ],
)
async def test_legacy_adapter_preserves_provider_parameter_baselines(
    provider_name: str,
    configured: dict[str, object],
    expected: dict[str, float | int],
) -> None:
    class _ConfiguredLegacy:
        def __init__(self) -> None:
            self.name = provider_name
            self.calls: list[dict[str, object]] = []
            for key, value in configured.items():
                setattr(self, key, value)

        async def is_available(self) -> bool:
            return True

        async def list_voices(self) -> list[dict[str, str]]:
            voice_id = "warm" if provider_name == "voisona" else f"{provider_name}-voice"
            return [{"id": voice_id, "name": voice_id}]

        async def synthesize(self, _text: str, **kwargs: object) -> AudioResult:
            self.calls.append(kwargs)
            return AudioResult(wav_bytes())

        async def close(self) -> None:
            return None

    legacy = _ConfiguredLegacy()
    service = SynthesisService(LegacyTTSProviderAdapter(legacy))

    await service.synthesize("configured baseline")

    native = legacy.calls[0]["_native_parameters"]
    assert isinstance(native, dict)
    for key, value in expected.items():
        assert native[key] == pytest.approx(value)
    if provider_name == "voisona":
        assert native["style_weights"] == [0.2, 0.8]
    await service.close()


@pytest.mark.asyncio
async def test_f07_voicepeak_native_values_and_narrator_reach_cli_exactly_once() -> None:
    class _Runner:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        async def run(self, command, *, deadline):
            self.commands.append(list(command))
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(wav_bytes())
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    provider = VoicepeakProvider(
        {
            "path": "voicepeak",
            "default_narrator": "default",
            "max_retries": 0,
        }
    )
    runner = _Runner()
    provider._process_runner = runner

    await provider.synthesize(
        "CLI境界",
        narrator="Japanese Female 1",
        _native_parameters={
            "speed": 125,
            "pitch": -150,
            "emotions": {"happy": 70},
        },
    )

    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command[command.index("-n") + 1] == "Japanese Female 1"
    assert command[command.index("--speed") + 1] == "125"
    assert command[command.index("--pitch") + 1] == "-150"
    assert command[command.index("-e") + 1] == "happy=70"
    await provider.close()


def test_f16_synthesis_request_preserves_scene_for_canonical_resolution() -> None:
    daily = Utterance(
        id="daily",
        order=0,
        text="日常",
        segment_type=SegmentType.NARRATION,
        scene="daily",
        emotion="neutral",
        intensity=0.0,
    )
    battle = Utterance(
        id="battle",
        order=1,
        text="戦闘",
        segment_type=SegmentType.DIALOGUE,
        scene="battle",
        emotion="angry",
        intensity=1.0,
    )

    daily_request = SynthesisRequest.from_utterance(daily)
    battle_request = SynthesisRequest.from_utterance(battle)

    assert daily_request.scene == "daily"
    assert battle_request.scene == "battle"
    assert daily_request.voice_intent != battle_request.voice_intent


def test_d01_tts_params_compatibility_adapter_runs_exactly_once() -> None:
    contract = _contract_module()
    from yomiage.tts.base import TTSParams

    legacy = TTSParams(speed=1.2, pitch=0.0, emotion="neutral", intensity=0.0)
    overrides = contract.VoiceOverrides.from_tts_params(legacy)
    first = contract.resolve_voice_parameters(VoiceIntent(), overrides=overrides)

    assert first.speaking_rate == 1.2
    assert first.pitch == 0.0
    assert first.intensity == 0.0
    assert not hasattr(first, "provider")


@pytest.mark.asyncio
async def test_d01_all_entrypoints_reach_provider_with_same_canonical_parameters(
    tmp_path: Path,
) -> None:
    provider = PortFakeProvider("voisona")
    service = SynthesisService(provider)
    intent = VoiceIntent(
        speaking_rate=1.2,
        pitch=0.1,
        volume=0.2,
        intonation=1.1,
        emotion="angry",
        intensity=0.8,
    )

    bridge = TTSBridge.from_service(service)
    await bridge.synthesize(
        "同じ発話",
        speed=1.2,
        pitch=0.1,
        volume=0.2,
        intonation=1.1,
        emotion="angry",
        intensity=0.8,
    )

    utterance = Utterance(
        id="reader",
        order=0,
        text="同じ発話",
        segment_type=SegmentType.NARRATION,
        voice_intent=intent,
        emotion="angry",
        intensity=0.8,
    )
    plan = UtterancePlan(
        schema_version=1,
        plan_id="parameter-plan",
        source_fingerprint="source",
        config_fingerprint="config",
        utterances=(utterance,),
    )
    queue = UtterancePlaybackQueue(service, play_audio=lambda _audio: None)
    await queue.play_plan(plan)

    engine = object.__new__(BatchEngine)
    engine.mode = "voisona"
    engine.output_dir = tmp_path
    engine.vm_mount = "Z:"
    entry = SentenceEntry(
        0,
        "同じ発話",
        0,
        "narration",
        emotion="angry",
        intensity=0.8,
        tts_params={
            "speed": 1.2,
            "pitch": 0.1,
            "volume": 0.2,
            "intonation": 1.1,
        },
    )
    manifest = BatchManifest(
        work_id="work",
        work_title="work",
        source_url="https://example.test",
        mode="voisona",
        run_id="run",
    )
    batch_request = engine._request_for_entry(
        entry,
        utterance,
        character_db=None,
        synthesizer=None,
        work_dir=tmp_path,
        manifest=manifest,
        service=service,
    )
    await service.synthesize(
        replace(
            batch_request,
            work_id=None,
            run_id=None,
            destination=str(tmp_path / "batch.wav"),
            target=None,
        )
    )

    studio = StudioSynthesizer(default_provider="voisona", synthesis_service=service)
    await studio.synthesize_line(
        ScriptLine(
            index=0,
            speaker="話者",
            text="同じ発話",
            original_text="同じ発話",
            emotion="angry",
            tts_params={"intensity": 0.8},
        ),
        SpeakerMapping(
            speaker="話者",
            provider="voisona",
            voice_id="voisona-voice",
            base_params={
                "speed": 1.2,
                "pitch": 0.1,
                "volume": 0.2,
                "intonation": 1.1,
            },
        ),
        tmp_path / "studio.wav",
    )

    assert len(provider.calls) == 4
    resolved = [request.resolved_parameters for request in provider.calls]
    assert all(value == resolved[0] for value in resolved), resolved
    assert resolved[0] is not None
    assert resolved[0].emotion is Emotion.ANGRY
    assert resolved[0].intensity == pytest.approx(0.8)
    await service.close()
