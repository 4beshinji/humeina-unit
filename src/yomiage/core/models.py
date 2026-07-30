"""Provider 非依存の音声読み上げドメインモデル.

このモジュールは ``core`` の最も内側に置くデータ契約だけを定義する。FastAPI、Typer、
TTS provider、filesystem の型はここへ持ち込まない。モデルは immutable な dataclass とし、
変換や永続化が必要な場合は ``to_dict`` / ``from_dict`` を明示的に呼び出す。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class SegmentType(str, Enum):
    """Utterance が表す文章の種類."""

    NARRATION = "narration"
    DIALOGUE = "dialogue"
    THOUGHT = "thought"
    SCENE_BREAK = "scene_break"


class Emotion(str, Enum):
    """コアで扱う provider 非依存の感情タグ."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    ANGRY = "angry"
    SAD = "sad"
    SURPRISED = "surprised"
    SCARED = "scared"
    GENTLE = "gentle"


# ``EmotionType`` は初期の変換コードで使いやすい別名として公開する。
EmotionType = Emotion
SegmentKind = SegmentType


def _require_non_empty_string(value: object, field_name: str) -> str:
    """文字列フィールドを正規化し、空値を拒否する."""

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _optional_string(value: object, field_name: str) -> str | None:
    """任意文字列を正規化する."""

    if value is None:
        return None
    return _require_non_empty_string(value, field_name)


def _coerce_segment_type(value: SegmentType | str) -> SegmentType:
    if isinstance(value, SegmentType):
        return value
    if not isinstance(value, str):
        raise TypeError("segment_type must be a SegmentType or string")
    try:
        return SegmentType(value.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SegmentType)
        raise ValueError(f"segment_type must be one of: {allowed}") from exc


def _coerce_emotion(value: Emotion | str) -> Emotion:
    if isinstance(value, Emotion):
        return value
    if not isinstance(value, str):
        raise TypeError("emotion must be an Emotion or string")
    try:
        return Emotion(value.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in Emotion)
        raise ValueError(f"emotion must be one of: {allowed}") from exc


def _finite_float(value: object, field_name: str) -> float:
    """数値を finite な float へ変換する（bool は数値として扱わない）."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _int_value(value: object, field_name: str, *, minimum: int | None = None) -> int:
    """整数フィールドを検証する（``True``/``False``を除く）."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _intensity(value: object) -> float:
    result = _finite_float(value, "intensity")
    if not 0.0 <= result <= 1.0:
        raise ValueError("intensity must be between 0.0 and 1.0")
    return result


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return value


def _coalesce_alias(
    value: object,
    aliases: Mapping[str, Any],
    field_name: str,
) -> object:
    """canonical keyとaliasの重複を検査して値を一つにする."""

    present = [alias for alias, candidate in aliases.items() if candidate is not None]
    if value is not None:
        if present:
            raise TypeError(f"pass only {field_name!r} or its alias, not both")
        return value
    if len(present) > 1:
        raise TypeError(f"pass only one alias for {field_name!r}")
    return aliases[present[0]] if present else None


@dataclass(frozen=True, slots=True, init=False)
class VoiceIntent:
    """Provider固有のvoice IDを含まない音声意図.

    ``speaking_rate``、``pitch``、``volume``、``intonation`` は provider に渡す前の
    正規化された相対値である。adapter はこの値から provider 固有パラメータを解決する。
    ``rate``/``speed``、``preferred_profile`` などは入力互換の alias であり、直列化では
    canonical なフィールド名を使用する。
    """

    archetype: str | None = None
    gender: str | None = None
    age_group: str | None = None
    speaking_rate: float = 1.0
    pitch: float = 0.0
    volume: float = 0.0
    intonation: float = 1.0
    emotion: Emotion = Emotion.NEUTRAL
    intensity: float = 0.5
    preferred_voice_profile: str | None = None

    def __init__(
        self,
        archetype: str | None = None,
        gender: str | None = None,
        age_group: str | None = None,
        speaking_rate: float | None = None,
        pitch: float = 0.0,
        volume: float = 0.0,
        intonation: float = 1.0,
        emotion: Emotion | str = Emotion.NEUTRAL,
        intensity: float = 0.5,
        preferred_voice_profile: str | None = None,
        *,
        gender_hint: str | None = None,
        age_hint: str | None = None,
        rate: float | None = None,
        speed: float | None = None,
        speaking_rate_ratio: float | None = None,
        preferred_profile: str | None = None,
        voice_profile: str | None = None,
    ) -> None:
        resolved_gender = _coalesce_alias(gender, {"gender_hint": gender_hint}, "gender")
        resolved_age = _coalesce_alias(age_group, {"age_hint": age_hint}, "age_group")
        resolved_rate = _coalesce_alias(
            speaking_rate,
            {"rate": rate, "speed": speed, "speaking_rate_ratio": speaking_rate_ratio},
            "speaking_rate",
        )
        resolved_profile = _coalesce_alias(
            preferred_voice_profile,
            {"preferred_profile": preferred_profile, "voice_profile": voice_profile},
            "preferred_voice_profile",
        )
        if resolved_rate is None:
            resolved_rate = 1.0
        rate_value = _finite_float(resolved_rate, "speaking_rate")
        if rate_value <= 0.0:
            raise ValueError("speaking_rate must be greater than 0.0")

        object.__setattr__(self, "archetype", _optional_string(archetype, "archetype"))
        object.__setattr__(self, "gender", _optional_string(resolved_gender, "gender"))
        object.__setattr__(self, "age_group", _optional_string(resolved_age, "age_group"))
        object.__setattr__(self, "speaking_rate", rate_value)
        object.__setattr__(self, "pitch", _finite_float(pitch, "pitch"))
        object.__setattr__(self, "volume", _finite_float(volume, "volume"))
        object.__setattr__(self, "intonation", _finite_float(intonation, "intonation"))
        object.__setattr__(self, "emotion", _coerce_emotion(emotion))
        object.__setattr__(self, "intensity", _intensity(intensity))
        object.__setattr__(
            self,
            "preferred_voice_profile",
            _optional_string(resolved_profile, "preferred_voice_profile"),
        )

    @property
    def gender_hint(self) -> str | None:
        """``gender`` の説明的な alias."""

        return self.gender

    @property
    def age_hint(self) -> str | None:
        """``age_group`` の説明的な alias."""

        return self.age_group

    @property
    def rate(self) -> float:
        """``speaking_rate`` の短い alias."""

        return self.speaking_rate

    @property
    def speed(self) -> float:
        """``speaking_rate`` の互換 alias."""

        return self.speaking_rate

    @property
    def speaking_rate_ratio(self) -> float:
        """``speaking_rate`` の説明的な alias."""

        return self.speaking_rate

    @property
    def preferred_profile(self) -> str | None:
        """``preferred_voice_profile`` の短い alias."""

        return self.preferred_voice_profile

    @property
    def voice_profile(self) -> str | None:
        """``preferred_voice_profile`` の互換 alias."""

        return self.preferred_voice_profile

    def to_dict(self) -> dict[str, Any]:
        """JSONに渡せる組み込み型だけの辞書へ変換する."""

        return {
            "archetype": self.archetype,
            "gender": self.gender,
            "age_group": self.age_group,
            "speaking_rate": self.speaking_rate,
            "pitch": self.pitch,
            "volume": self.volume,
            "intonation": self.intonation,
            "emotion": self.emotion.value,
            "intensity": self.intensity,
            "preferred_voice_profile": self.preferred_voice_profile,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VoiceIntent:
        """辞書からVoiceIntentを復元する."""

        raw = _mapping(data, "voice_intent")
        return cls(
            archetype=raw.get("archetype"),
            gender=raw.get("gender", raw.get("gender_hint")),
            age_group=raw.get("age_group", raw.get("age_hint")),
            speaking_rate=raw.get(
                "speaking_rate",
                raw.get("rate", raw.get("speed", raw.get("speaking_rate_ratio"))),
            ),
            pitch=raw.get("pitch", 0.0),
            volume=raw.get("volume", 0.0),
            intonation=raw.get("intonation", 1.0),
            emotion=raw.get("emotion", Emotion.NEUTRAL),
            intensity=raw.get("intensity", 0.5),
            preferred_voice_profile=raw.get(
                "preferred_voice_profile",
                raw.get("preferred_profile", raw.get("voice_profile")),
            ),
        )


@dataclass(frozen=True, slots=True, init=False)
class SourceRef:
    """原文の位置を指す provider 非依存の参照."""

    url: str | None = None
    chapter: str | int | None = None
    start: int | None = None
    end: int | None = None
    locator: str | None = None

    def __init__(
        self,
        url: str | None = None,
        chapter: str | int | None = None,
        start: int | None = None,
        end: int | None = None,
        locator: str | None = None,
        *,
        source_url: str | None = None,
        chapter_id: str | int | None = None,
        chapter_index: int | None = None,
        start_offset: int | None = None,
        end_offset: int | None = None,
    ) -> None:
        resolved_url = _coalesce_alias(url, {"source_url": source_url}, "url")
        resolved_chapter = _coalesce_alias(
            chapter,
            {"chapter_id": chapter_id, "chapter_index": chapter_index},
            "chapter",
        )
        resolved_start = _coalesce_alias(start, {"start_offset": start_offset}, "start")
        resolved_end = _coalesce_alias(end, {"end_offset": end_offset}, "end")
        if resolved_chapter is not None and not isinstance(resolved_chapter, (str, int)):
            raise TypeError("chapter must be a string or integer")
        if isinstance(resolved_chapter, bool):
            raise TypeError("chapter must be a string or integer")
        if isinstance(resolved_chapter, str):
            resolved_chapter = _require_non_empty_string(resolved_chapter, "chapter")
        elif isinstance(resolved_chapter, int) and resolved_chapter < 0:
            raise ValueError("chapter must be >= 0")

        start_value = (
            None
            if resolved_start is None
            else _int_value(resolved_start, "start", minimum=0)
        )
        end_value = None if resolved_end is None else _int_value(resolved_end, "end", minimum=0)
        if start_value is not None and end_value is not None and end_value < start_value:
            raise ValueError("end must be greater than or equal to start")

        object.__setattr__(self, "url", _optional_string(resolved_url, "url"))
        object.__setattr__(self, "chapter", resolved_chapter)
        object.__setattr__(self, "start", start_value)
        object.__setattr__(self, "end", end_value)
        object.__setattr__(self, "locator", _optional_string(locator, "locator"))

    @property
    def source_url(self) -> str | None:
        return self.url

    @property
    def chapter_id(self) -> str | int | None:
        return self.chapter

    @property
    def chapter_index(self) -> int | None:
        return self.chapter if isinstance(self.chapter, int) else None

    @property
    def start_offset(self) -> int | None:
        return self.start

    @property
    def end_offset(self) -> int | None:
        return self.end

    @property
    def start_char(self) -> int | None:
        return self.start

    @property
    def end_char(self) -> int | None:
        return self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "chapter": self.chapter,
            "start": self.start,
            "end": self.end,
            "locator": self.locator,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceRef:
        raw = _mapping(data, "source_ref")
        return cls(
            url=raw.get("url", raw.get("source_url")),
            chapter=raw.get(
                "chapter",
                raw.get("chapter_id", raw.get("chapter_index")),
            ),
            start=raw.get("start", raw.get("start_offset")),
            end=raw.get("end", raw.get("end_offset")),
            locator=raw.get("locator"),
        )


class WarningSeverity(str, Enum):
    """Plan warningの重大度."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True, init=False)
class PlanWarning:
    """解析計画に残す、再現可能な警告."""

    code: str
    message: str
    severity: WarningSeverity = WarningSeverity.WARNING
    utterance_id: str | None = None

    def __init__(
        self,
        code: str,
        message: str,
        severity: WarningSeverity | str = WarningSeverity.WARNING,
        utterance_id: str | None = None,
        *,
        kind: str | None = None,
        detail: str | None = None,
    ) -> None:
        resolved_code = _coalesce_alias(code, {"kind": kind}, "code")
        resolved_message = _coalesce_alias(message, {"detail": detail}, "message")
        assert resolved_code is not None
        assert resolved_message is not None
        try:
            severity_value = (
                severity
                if isinstance(severity, WarningSeverity)
                else WarningSeverity(str(severity).strip().lower())
            )
        except ValueError as exc:
            allowed = ", ".join(item.value for item in WarningSeverity)
            raise ValueError(f"severity must be one of: {allowed}") from exc
        object.__setattr__(self, "code", _require_non_empty_string(resolved_code, "code"))
        object.__setattr__(
            self,
            "message",
            _require_non_empty_string(resolved_message, "message"),
        )
        object.__setattr__(self, "severity", severity_value)
        object.__setattr__(self, "utterance_id", _optional_string(utterance_id, "utterance_id"))

    @property
    def kind(self) -> str:
        return self.code

    @property
    def detail(self) -> str:
        return self.message

    @property
    def level(self) -> WarningSeverity:
        return self.severity

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "utterance_id": self.utterance_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanWarning:
        raw = _mapping(data, "warning")
        return cls(
            code=raw.get("code", raw.get("kind")),
            message=raw.get("message", raw.get("detail")),
            severity=raw.get("severity", WarningSeverity.WARNING),
            utterance_id=raw.get("utterance_id"),
        )


# 変換層が採用する名称の揺れを吸収する。canonicalな直列化名はPlanWarningである。
WarningRecord = PlanWarning
UtteranceWarning = PlanWarning
DomainWarning = PlanWarning
# ``Warning`` は公開契約を読む側が自然に参照できる短い別名でもある。組み込みのWarningを
# このモジュールの外へ上書きすることはなく、``from yomiage.core.models import Warning``
# と明示した箇所だけで解決される。
Warning = PlanWarning


@dataclass(frozen=True, slots=True, init=False)
class Utterance:
    """一人の話者と一つの音声意図で連続して読める最小単位."""

    id: str
    order: int
    text: str
    segment_type: SegmentType
    speaker_id: str | None = None
    scene: str = "daily"
    emotion: Emotion = Emotion.NEUTRAL
    intensity: float = 0.5
    voice_intent: VoiceIntent = VoiceIntent()
    source_ref: SourceRef | None = None

    def __init__(
        self,
        id: str,
        order: int,
        text: str,
        segment_type: SegmentType | str,
        speaker_id: str | None = None,
        scene: str = "daily",
        emotion: Emotion | str = Emotion.NEUTRAL,
        intensity: float = 0.5,
        voice_intent: VoiceIntent | Mapping[str, Any] | None = None,
        source_ref: SourceRef | Mapping[str, Any] | None = None,
        *,
        speaker: str | None = None,
    ) -> None:
        if speaker_id is not None and speaker is not None:
            raise TypeError("pass only speaker_id or speaker, not both")
        resolved_speaker = speaker_id if speaker_id is not None else speaker
        object.__setattr__(self, "id", _require_non_empty_string(id, "id"))
        object.__setattr__(self, "order", _int_value(order, "order", minimum=0))
        object.__setattr__(self, "text", _require_non_empty_string(text, "text"))
        object.__setattr__(self, "segment_type", _coerce_segment_type(segment_type))
        object.__setattr__(self, "speaker_id", _optional_string(resolved_speaker, "speaker_id"))
        object.__setattr__(self, "scene", _require_non_empty_string(scene, "scene"))
        object.__setattr__(self, "emotion", _coerce_emotion(emotion))
        object.__setattr__(self, "intensity", _intensity(intensity))
        if voice_intent is None:
            intent = VoiceIntent(emotion=emotion, intensity=intensity)
        elif isinstance(voice_intent, VoiceIntent):
            intent = voice_intent
        else:
            intent = VoiceIntent.from_dict(_mapping(voice_intent, "voice_intent"))
        if source_ref is None or isinstance(source_ref, SourceRef):
            ref = source_ref
        else:
            ref = SourceRef.from_dict(_mapping(source_ref, "source_ref"))
        object.__setattr__(self, "voice_intent", intent)
        object.__setattr__(self, "source_ref", ref)

    @property
    def speaker(self) -> str | None:
        """既存NLP結果との変換時に使える alias."""

        return self.speaker_id

    @property
    def type(self) -> SegmentType:
        """既存NLPの ``type`` フィールドとの変換時に使える alias."""

        return self.segment_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order": self.order,
            "text": self.text,
            "segment_type": self.segment_type.value,
            "speaker_id": self.speaker_id,
            "scene": self.scene,
            "emotion": self.emotion.value,
            "intensity": self.intensity,
            "voice_intent": self.voice_intent.to_dict(),
            "source_ref": self.source_ref.to_dict() if self.source_ref is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Utterance:
        raw = _mapping(data, "utterance")
        if "id" not in raw:
            raise ValueError("utterance.id is required")
        if "order" not in raw:
            raise ValueError("utterance.order is required")
        if "text" not in raw:
            raise ValueError("utterance.text is required")
        if "segment_type" not in raw:
            raise ValueError("utterance.segment_type is required")
        return cls(
            id=raw["id"],
            order=raw["order"],
            text=raw["text"],
            segment_type=raw["segment_type"],
            speaker_id=raw.get("speaker_id", raw.get("speaker")),
            scene=raw.get("scene", "daily"),
            emotion=raw.get("emotion", Emotion.NEUTRAL),
            intensity=raw.get("intensity", 0.5),
            voice_intent=raw.get("voice_intent"),
            source_ref=raw.get("source_ref"),
        )


# Pythonの組み込み ``Warning`` を隠すことは避け、ドメインで明示的に使える別名だけ公開する。
__all__ = [
    "DomainWarning",
    "Emotion",
    "EmotionType",
    "PlanWarning",
    "SegmentKind",
    "SegmentType",
    "SourceRef",
    "Utterance",
    "UtteranceWarning",
    "VoiceIntent",
    "Warning",
    "WarningRecord",
    "WarningSeverity",
]
