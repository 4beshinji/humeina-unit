"""Provider非依存の音声parameter解決契約。

値は ``unset override``、``VoiceIntent``、``resolved absolute value`` の段階を分ける。
provider-native値への変換はこのmoduleのresolved値を入力としてadapter境界で行う。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..core.models import Emotion, VoiceIntent
from ..tts.base import TTSParams


def _optional_finite(value: float | int | None, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number or None")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _emotion(value: Emotion | str | None) -> Emotion | None:
    if value is None:
        return None
    return value if isinstance(value, Emotion) else Emotion(str(value).lower())


@dataclass(frozen=True, slots=True)
class VoiceOverrides:
    """利用者が明示した値だけを保持するcompatibility入力。"""

    speaking_rate: float | None = None
    pitch: float | None = None
    volume: float | None = None
    intonation: float | None = None
    huskiness: float | None = None
    alp: float | None = None
    style_weights: tuple[float, ...] | None = None
    emotion: Emotion | None = None
    intensity: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "speaking_rate",
            "pitch",
            "volume",
            "intonation",
            "huskiness",
            "alp",
            "intensity",
        ):
            object.__setattr__(self, name, _optional_finite(getattr(self, name), name))
        if self.speaking_rate is not None and self.speaking_rate <= 0:
            raise ValueError("speaking_rate must be greater than zero")
        if self.intensity is not None and not 0 <= self.intensity <= 1:
            raise ValueError("intensity must be between 0 and 1")
        object.__setattr__(self, "emotion", _emotion(self.emotion))
        if self.style_weights is not None:
            object.__setattr__(
                self,
                "style_weights",
                tuple(float(value) for value in self.style_weights),
            )

    @classmethod
    def from_tts_params(cls, params: TTSParams) -> VoiceOverrides:
        """公開互換DTOをcanonical overrideへ一度だけ変換する。"""

        if not isinstance(params, TTSParams):
            raise TypeError("params must be TTSParams")
        return cls(
            speaking_rate=params.speed,
            pitch=params.pitch,
            volume=params.volume,
            intonation=params.intonation,
            huskiness=params.huskiness,
            alp=params.alp,
            style_weights=(
                tuple(params.style_weights) if params.style_weights is not None else None
            ),
            emotion=params.emotion,
            intensity=params.intensity,
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> VoiceOverrides:
        """Convert one legacy parameter mapping without inventing unset values."""

        if not isinstance(values, Mapping):
            raise TypeError("values must be a mapping")

        def present(*names: str) -> Any | None:
            return next((values[name] for name in names if name in values), None)

        style = present("style_weights")
        return cls(
            speaking_rate=present("speaking_rate", "speed"),
            pitch=present("pitch"),
            volume=present("volume"),
            intonation=present("intonation"),
            huskiness=present("huskiness"),
            alp=present("alp"),
            style_weights=tuple(style) if style is not None else None,
            emotion=present("emotion"),
            intensity=present("intensity"),
        )


@dataclass(frozen=True, slots=True)
class ResolvedVoiceParameters:
    """profile/scene/emotion/override適用後のprovider非依存絶対値。"""

    speaking_rate: float = 1.0
    pitch: float = 0.0
    volume: float = 0.0
    intonation: float = 1.0
    huskiness: float = 0.0
    alp: float = 0.0
    style_weights: tuple[float, ...] = ()
    emotion: Emotion = Emotion.NEUTRAL
    intensity: float = 0.5

    def __post_init__(self) -> None:
        if self.speaking_rate <= 0:
            raise ValueError("speaking_rate must be greater than zero")
        if not 0 <= self.intensity <= 1:
            raise ValueError("intensity must be between 0 and 1")
        object.__setattr__(self, "emotion", _emotion(self.emotion) or Emotion.NEUTRAL)
        object.__setattr__(self, "style_weights", tuple(self.style_weights))


@dataclass(frozen=True, slots=True)
class ProviderParameterSet:
    """adapter内だけで利用するprovider-native parameter。"""

    provider: str
    values: Mapping[str, float | int | str | tuple[float, ...] | Mapping[str, int]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        provider = str(self.provider).strip().lower()
        if not provider:
            raise ValueError("provider must not be empty")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "values", dict(self.values))


@dataclass(frozen=True, slots=True)
class VoiceResolutionContext:
    """入口が選択したprovider非依存のprofile/scene/emotion設定。"""

    profile: Mapping[str, Any] = field(default_factory=dict)
    scene: Mapping[str, Any] = field(default_factory=dict)
    emotion: Mapping[str, Any] = field(default_factory=dict)
    overrides: VoiceOverrides = field(default_factory=VoiceOverrides)
    provider_options: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", dict(self.profile))
        object.__setattr__(self, "scene", dict(self.scene))
        object.__setattr__(self, "emotion", dict(self.emotion))
        object.__setattr__(
            self, "provider_options", {k: dict(v) for k, v in self.provider_options.items()}
        )


def _number(mapping: Mapping[str, Any] | None, *names: str, default: float) -> float:
    source = mapping or {}
    for name in names:
        value = source.get(name)
        if value is not None:
            resolved = _optional_finite(value, name)
            assert resolved is not None
            return resolved
    return default


def _blend_style(weights: Sequence[float], intensity: float) -> tuple[float, ...]:
    values = tuple(float(value) for value in weights)
    if not values:
        return ()
    neutral = (1.0,) + (0.0,) * (len(values) - 1)
    return tuple(base + (target - base) * intensity for base, target in zip(neutral, values))


def resolve_voice_parameters(
    intent: VoiceIntent,
    *,
    profile: Mapping[str, Any] | None = None,
    scene: Mapping[str, Any] | None = None,
    emotion: Mapping[str, Any] | None = None,
    overrides: VoiceOverrides | None = None,
) -> ResolvedVoiceParameters:
    """profile → scene → emotion → explicit override の順で一度だけ合成する。"""

    if not isinstance(intent, VoiceIntent):
        raise TypeError("intent must be VoiceIntent")
    explicit = overrides or VoiceOverrides()
    intensity = explicit.intensity if explicit.intensity is not None else intent.intensity
    resolved_emotion = explicit.emotion or intent.emotion

    rate = _number(profile, "speaking_rate", "speed", default=1.0)
    pitch = _number(profile, "pitch", default=0.0)
    volume = _number(profile, "volume", default=0.0)
    intonation = _number(profile, "intonation", default=1.0)
    huskiness = _number(profile, "huskiness", default=0.0)
    alp = _number(profile, "alp", default=0.0)

    rate *= intent.speaking_rate
    pitch += intent.pitch
    volume += intent.volume
    intonation *= intent.intonation

    rate *= _number(scene, "speaking_rate", "speed", default=1.0)
    pitch += _number(scene, "pitch", default=0.0)
    volume += _number(scene, "volume", default=0.0)
    intonation *= _number(scene, "intonation", default=1.0)
    huskiness += _number(scene, "huskiness", default=0.0)
    alp += _number(scene, "alp", default=0.0)

    offsets = emotion.get("param_offsets", {}) if emotion else {}
    if not isinstance(offsets, Mapping):
        raise TypeError("emotion.param_offsets must be a mapping")
    rate *= 1.0 + (_number(offsets, "speaking_rate", "speed", default=1.0) - 1.0) * intensity
    pitch += _number(offsets, "pitch", default=0.0) * intensity
    volume += _number(offsets, "volume", default=0.0) * intensity
    intonation *= 1.0 + (_number(offsets, "intonation", default=1.0) - 1.0) * intensity
    huskiness += _number(offsets, "huskiness", default=0.0) * intensity
    alp += _number(offsets, "alp", default=0.0) * intensity

    raw_style = emotion.get("style_weights") if emotion else None
    style = _blend_style(raw_style, intensity) if isinstance(raw_style, Sequence) else ()

    return ResolvedVoiceParameters(
        speaking_rate=explicit.speaking_rate if explicit.speaking_rate is not None else rate,
        pitch=explicit.pitch if explicit.pitch is not None else pitch,
        volume=explicit.volume if explicit.volume is not None else volume,
        intonation=explicit.intonation if explicit.intonation is not None else intonation,
        huskiness=explicit.huskiness if explicit.huskiness is not None else huskiness,
        alp=explicit.alp if explicit.alp is not None else alp,
        style_weights=(explicit.style_weights if explicit.style_weights is not None else style),
        emotion=resolved_emotion,
        intensity=intensity,
    )


def translate_provider_parameters(
    provider: str,
    resolved: ResolvedVoiceParameters,
) -> ProviderParameterSet:
    """resolved absolute valueをprovider-native値へ一度だけ変換する。"""

    name = str(provider).strip().lower()
    if name == "voisona":
        values: dict[str, Any] = {
            "speed": resolved.speaking_rate,
            "pitch": resolved.pitch,
            "volume": resolved.volume,
            "intonation": resolved.intonation,
            "huskiness": resolved.huskiness,
            "alp": resolved.alp,
        }
        if resolved.style_weights:
            values["style_weights"] = resolved.style_weights
    elif name == "voicevox":
        values = {
            "speedScale": resolved.speaking_rate,
            "pitchScale": resolved.pitch,
            "volumeScale": 1.0 + resolved.volume * 0.1,
            "intonationScale": resolved.intonation,
        }
    elif name == "voicepeak":
        values = {
            "speed": max(50, min(200, round(resolved.speaking_rate * 100))),
            "pitch": max(-300, min(300, round(resolved.pitch * 300))),
        }
    else:
        raise ValueError(f"unsupported provider parameter translator: {provider}")
    return ProviderParameterSet(name, values)


__all__ = [
    "ResolvedVoiceParameters",
    "ProviderParameterSet",
    "VoiceOverrides",
    "VoiceResolutionContext",
    "resolve_voice_parameters",
    "translate_provider_parameters",
]
