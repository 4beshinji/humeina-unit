"""NLP 結果を provider 非依存の :class:`UtterancePlan` へ変換する。

このモジュールは、Reader/API が返す ``AnalyzedSegment`` と、旧 Batch が保存する
``SentenceEntry`` の差を吸収する移行用 adapter である。文字数による TTS 用分割は
ここでは行わない。ここで確定するのは、話者・音声意図・文章種別が変わる発話境界だけ
である。

``nlp`` の型をコアへ漏らさないため、変換時は属性/辞書の小さな構造を読む。これにより
旧 manifest、公開 API の ``AnalysisResult``、新しい NLP adapter の結果を同じコードで
扱える。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..core import identity as core_identity
from ..core.models import (
    Emotion,
    PlanWarning,
    SegmentType,
    SourceRef,
    Utterance,
    VoiceIntent,
)
from ..core.plan import CURRENT_SCHEMA_VERSION, UtterancePlan
from ..core.text import TextDocument

if TYPE_CHECKING:
    from ..batch.manifest import BatchManifest
    from ..nlp.pipeline import NLPAnalyzer


_SEGMENT_TYPE_ALIASES = {
    "dialog": SegmentType.DIALOGUE.value,
    "dialogue": SegmentType.DIALOGUE.value,
    "speech": SegmentType.DIALOGUE.value,
    "narration": SegmentType.NARRATION.value,
    "narrative": SegmentType.NARRATION.value,
    "thought": SegmentType.THOUGHT.value,
    "inner_thought": SegmentType.THOUGHT.value,
    "monologue": SegmentType.THOUGHT.value,
    "scene_break": SegmentType.SCENE_BREAK.value,
    "scene-break": SegmentType.SCENE_BREAK.value,
    "break": SegmentType.SCENE_BREAK.value,
}

_EMOTION_ALIASES = {
    "calm": Emotion.NEUTRAL.value,
    "normal": Emotion.NEUTRAL.value,
    "joy": Emotion.HAPPY.value,
    "joyful": Emotion.HAPPY.value,
    "happiness": Emotion.HAPPY.value,
    "furious": Emotion.ANGRY.value,
    "mad": Emotion.ANGRY.value,
    "fear": Emotion.SCARED.value,
    "afraid": Emotion.SCARED.value,
    "shock": Emotion.SURPRISED.value,
    "surprise": Emotion.SURPRISED.value,
    "tender": Emotion.GENTLE.value,
}


def _value(item: object, name: str, *aliases: str, default: Any = None) -> Any:
    """属性または mapping から値を読む。``None`` は alias を順に試す。"""

    names = (name, *aliases)
    if isinstance(item, Mapping):
        for key in names:
            if key in item and item[key] is not None:
                return item[key]
        return default
    for key in names:
        candidate = getattr(item, key, None)
        if candidate is not None:
            return candidate
    return default


def _as_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_segment_type(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    normalized = str(value or SegmentType.NARRATION.value).strip().lower().replace(" ", "_")
    return _SEGMENT_TYPE_ALIASES.get(normalized, SegmentType.NARRATION.value)


def _normalize_emotion(value: object) -> str:
    if isinstance(value, Emotion):
        return value.value
    normalized = str(value or Emotion.NEUTRAL.value).strip().lower().replace(" ", "_")
    normalized = _EMOTION_ALIASES.get(normalized, normalized)
    try:
        return Emotion(normalized).value
    except ValueError:
        # LLM の未知タグで plan 全体を破棄しない。精度改善はこのタスクの範囲外なので
        # 安全な中立値へ倒す。
        return Emotion.NEUTRAL.value


def _normalize_intensity(value: object) -> float:
    if isinstance(value, bool):
        return 0.5
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(result):
        return 0.5
    return max(0.0, min(1.0, result))


def _source_ref(
    value: object, *, source: str | None = None, chapter: object = None
) -> SourceRef | None:
    """旧入力の source metadata をコア SourceRef へ変換する。"""

    if isinstance(value, SourceRef):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return SourceRef(url=value)
        except (TypeError, ValueError):
            pass
    if isinstance(value, Mapping):
        try:
            return SourceRef.from_dict({"url": source, "chapter": chapter, **value})
        except (TypeError, ValueError):
            # 不正な optional locator は本文を失敗させず、source URL だけを残す。
            pass
    if source is None and chapter is None:
        return None
    kwargs: dict[str, Any] = {}
    if source:
        kwargs["url"] = source
    if chapter is not None:
        # ``chapter_index`` は int として保存できない入力もあるため、変換に失敗した
        # 場合は locator として扱う。
        if isinstance(chapter, bool):
            kwargs["locator"] = str(chapter)
        elif isinstance(chapter, (int, str)):
            kwargs["chapter"] = chapter
    try:
        return SourceRef(**kwargs) if kwargs else None
    except (TypeError, ValueError):
        return SourceRef(url=source) if source else None


def _mapping_or_empty(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class PlanningContext:
    """Plan の identity/source metadata。

    ``source`` は URL でなくてもよい。Reader の一時入力では本文そのものを source と
    して使うことで、同じ本文から安定した fingerprint を生成できる。
    """

    source: str
    config: object = None
    source_kind: str | None = None
    plan_id: str | None = None
    source_fingerprint: str | None = None
    config_fingerprint: str | None = None
    created_at: datetime | str | None = None

    @classmethod
    def for_input(
        cls,
        source: str | None,
        *,
        text: str = "",
        config: object = None,
        source_kind: str | None = None,
        plan_id: str | None = None,
        source_fp: str | None = None,
        config_fp: str | None = None,
        created_at: datetime | str | None = None,
    ) -> "PlanningContext":
        resolved_source = _as_text(source) or text
        if not resolved_source:
            resolved_source = "<empty>"
        resolved_config = config if config is not None else {}
        return cls(
            source=resolved_source,
            config=resolved_config,
            source_kind=source_kind,
            plan_id=plan_id,
            source_fingerprint=source_fp,
            config_fingerprint=config_fp,
            created_at=created_at,
        )


@dataclass(slots=True)
class _NormalizedSegment:
    """コアへ渡す直前の provider 非依存値。"""

    text: str
    segment_type: str
    speaker: str | None
    scene: str
    emotion: str
    intensity: float
    voice_intent: VoiceIntent
    source_ref: SourceRef | None
    explicit_boundary: bool = False
    text_content: TextDocument | None = None
    speaker_origin: str = "default"
    unresolved_reason: str | None = None


def _intent_for(item: object, *, emotion: str, intensity: float) -> VoiceIntent:
    metadata = _mapping_or_empty(_value(item, "metadata", default={}))
    raw = _value(item, "voice_intent", "voice", default=None)
    if raw is None:
        raw = metadata.get("voice_intent", metadata.get("voice", None))
    payload: dict[str, Any] = _mapping_or_empty(raw)
    # Legacy metadata names are useful when Batch character discovery supplied a profile.
    for target, names in {
        "archetype": ("archetype", "voice_archetype"),
        "gender": ("gender", "gender_hint"),
        "age_group": ("age_group", "age_hint"),
        "preferred_voice_profile": (
            "preferred_voice_profile",
            "preferred_profile",
            "voice_profile",
        ),
    }.items():
        if target not in payload:
            for name in names:
                if metadata.get(name) is not None:
                    payload[target] = metadata[name]
                    break
    payload.setdefault("emotion", emotion)
    payload.setdefault("intensity", intensity)
    try:
        return raw if isinstance(raw, VoiceIntent) else VoiceIntent.from_dict(payload)
    except (TypeError, ValueError):
        # Invalid optional voice metadata must not erase an otherwise usable plan.
        return VoiceIntent(emotion=emotion, intensity=intensity)


def _normalize_item(
    item: object,
    *,
    source: str | None,
    chapter: object = None,
) -> _NormalizedSegment | None:
    text = _as_text(_value(item, "text", default=""))
    segment_type = _normalize_segment_type(
        _value(item, "segment_type", "type", default="narration")
    )
    if segment_type == SegmentType.SCENE_BREAK.value:
        # Core Utterance は空本文を許さず、scene break は TTS 対象でもない。呼び出し側で
        # warning を付与できるよう None を返す。
        return None
    if not text:
        return None

    speaker = _value(item, "speaker_id", "speaker", default=None)
    speaker_text = _as_text(speaker) or None
    scene = _as_text(_value(item, "scene", default="daily")) or "daily"
    emotion = _normalize_emotion(_value(item, "emotion", default=Emotion.NEUTRAL.value))
    intensity = _normalize_intensity(_value(item, "intensity", default=0.5))
    metadata = _mapping_or_empty(_value(item, "metadata", default={}))
    ref = _value(item, "source_ref", "source", default=None)
    if ref is None:
        ref = metadata.get("source_ref")
    ref = _source_ref(
        ref,
        source=source,
        chapter=_value(item, "chapter_index", "chapter", default=chapter),
    )
    intent = _intent_for(item, emotion=emotion, intensity=intensity)
    explicit_boundary = bool(metadata.get("boundary", metadata.get("force_boundary", False)))
    return _NormalizedSegment(
        text=text,
        segment_type=segment_type,
        speaker=speaker_text,
        scene=scene,
        emotion=emotion,
        intensity=intensity,
        voice_intent=intent,
        source_ref=ref,
        explicit_boundary=explicit_boundary,
        text_content=_value(item, "text_content", default=metadata.get("text_content")),
        speaker_origin=_value(
            item, "speaker_origin", default=metadata.get("speaker_origin", "default")
        ),
        unresolved_reason=_value(
            item, "unresolved_reason", default=metadata.get("unresolved_reason")
        ),
    )


def _flatten_results(results: object) -> tuple[list[object], bool]:
    """ChunkAnalysis/flat result/manifest を順序を壊さず平坦化する。

    Returns ``(items, had_scene_break)``.  ``ChunkAnalysis`` の chunk index はチャンクごとに
    resetされるため、全体 sort は行わず入力順を信頼する。
    """

    if results is None:
        return [], False
    sentences = getattr(results, "sentences", None)
    if sentences is not None and not isinstance(results, (str, bytes, bytearray)):
        raw_items = list(sentences)
        # BatchManifest の sentence index は global order。旧 JSON 読み込みで順番が崩れて
        # いても同じ plan を再現できるよう明示 index で安定化する。
        raw_items.sort(key=lambda value: (_value(value, "index", default=0),))
        return raw_items, any(
            _normalize_segment_type(_value(item, "segment_type", "type", default="narration"))
            == SegmentType.SCENE_BREAK.value
            for item in raw_items
        )
    if isinstance(results, Mapping):
        for key in ("segments", "results", "analyses", "utterances", "sentences"):
            if key in results:
                return _flatten_results(results[key])
        return [results], False
    if isinstance(results, (str, bytes, bytearray)):
        return [], False
    if not isinstance(results, Iterable):
        return [results], False

    flattened: list[object] = []
    had_scene_break = False
    for value in results:
        segments = getattr(value, "segments", None)
        if segments is not None and not isinstance(value, (str, bytes, bytearray)):
            nested, nested_break = _flatten_results(segments)
            flattened.extend(nested)
            had_scene_break = had_scene_break or nested_break
            continue
        flattened.append(value)
        had_scene_break = had_scene_break or (
            _normalize_segment_type(_value(value, "segment_type", "type", default="narration"))
            == SegmentType.SCENE_BREAK.value
        )
    return flattened, had_scene_break


class UtterancePlanner:
    """既存 NLP の結果から ``UtterancePlan`` を生成する application service."""

    def __init__(
        self,
        analyzer: "NLPAnalyzer | None" = None,
        *,
        nlp_analyzer: "NLPAnalyzer | None" = None,
    ):
        if analyzer is not None and nlp_analyzer is not None:
            raise TypeError("pass only analyzer or nlp_analyzer")
        self.analyzer = analyzer if analyzer is not None else nlp_analyzer

    def build(
        self,
        results: object,
        *,
        source: str | None = None,
        config: object = None,
        source_kind: str | None = None,
        plan_id: str | None = None,
        source_fingerprint: str | None = None,
        config_fingerprint: str | None = None,
        source_fingerprint_value: str | None = None,
        config_fingerprint_value: str | None = None,
        created_at: datetime | str | None = None,
        merge_adjacent: bool = True,
        chapter: object = None,
    ) -> UtterancePlan:
        """分析結果を plan へ変換する（同期）。

        ``merge_adjacent`` は既定で有効。連続する同一話者・同一音声意図・同一文章種別
        は一つへまとめ、話者または voice intent の変更では必ず境界を作る。
        """

        items, had_scene_break = _flatten_results(results)
        context = PlanningContext.for_input(
            source,
            text="\n".join(_as_text(_value(item, "text", default="")) for item in items),
            config=config,
            source_kind=source_kind,
            plan_id=plan_id,
            source_fp=source_fingerprint or source_fingerprint_value,
            config_fp=config_fingerprint or config_fingerprint_value,
            created_at=created_at,
        )
        normalized: list[_NormalizedSegment] = []
        source_ref_source = _as_text(source) or None
        for item in items:
            value = _normalize_item(item, source=source_ref_source, chapter=chapter)
            if value is not None:
                normalized.append(value)

        grouped: list[_NormalizedSegment] = []
        for value in normalized:
            if not merge_adjacent or not grouped or not self._can_merge(grouped[-1], value):
                grouped.append(value)
                continue
            previous = grouped[-1]
            previous.text += value.text
            # Keep the first source reference. Its chapter is enough for old manifest imports;
            # exact offsets, when supplied, are already preserved by the first item.

        utterances = tuple(
            Utterance(
                id=f"u-{order:04d}",
                order=order,
                text=value.text,
                segment_type=value.segment_type,
                speaker_id=value.speaker,
                scene=value.scene,
                emotion=value.emotion,
                intensity=value.intensity,
                voice_intent=value.voice_intent,
                source_ref=value.source_ref,
                text_content=value.text_content,
                speaker_origin=value.speaker_origin,
                unresolved_reason=value.unresolved_reason,
            )
            for order, value in enumerate(grouped)
        )

        warnings: list[PlanWarning] = []
        if had_scene_break:
            warnings.append(
                PlanWarning(
                    code="SCENE_BREAK_SKIPPED",
                    message="scene break is a navigation marker and is not synthesized",
                    severity="info",
                )
            )
        if self.analyzer is not None and getattr(self.analyzer, "scene_analyzer", None) is None:
            warnings.append(
                PlanWarning(
                    code="NLP_FALLBACK",
                    message="rule-based values were used without LLM scene analysis",
                    severity="info",
                )
            )

        source_value = context.source
        source_fp = context.source_fingerprint or core_identity.source_fingerprint(
            source_value,
            source_kind=context.source_kind,
        )
        config_fp = context.config_fingerprint or core_identity.config_fingerprint(
            context.config or {}
        )
        resolved_plan_id = context.plan_id or core_identity.plan_id_for_source(
            source_value, context.config or {}, source_kind=context.source_kind
        )
        return UtterancePlan(
            schema_version=CURRENT_SCHEMA_VERSION,
            plan_id=resolved_plan_id,
            source_fingerprint=source_fp,
            config_fingerprint=config_fp,
            created_at=context.created_at or datetime.now(UTC),
            utterances=utterances,
            warnings=tuple(warnings),
            source_documents=tuple(getattr(results, "source_documents", ())),
        )

    # Names used by different entrypoints during migration. Keep one implementation so all
    # adapters have exactly the same ordering and boundary semantics.
    from_results = build
    build_from_nlp = build
    from_analysis = build
    to_plan = build
    create_plan = build

    @staticmethod
    def _can_merge(left: _NormalizedSegment, right: _NormalizedSegment) -> bool:
        if left.text_content is not None or right.text_content is not None:
            return False
        if left.explicit_boundary or right.explicit_boundary:
            return False
        return (
            left.segment_type == right.segment_type
            and left.speaker == right.speaker
            and left.scene == right.scene
            and left.emotion == right.emotion
            and left.intensity == right.intensity
            and left.voice_intent == right.voice_intent
            and left.source_ref == right.source_ref
        )

    async def from_text(
        self,
        text: str,
        *,
        source: str | None = None,
        config: object = None,
        source_kind: str | None = None,
        known_characters: list[str] | None = None,
        character_db: object = None,
        **kwargs: Any,
    ) -> UtterancePlan:
        """TextProcessor/classifier/speaker/scene analyzer を通して plan を作る。"""

        if not isinstance(text, (str, TextDocument)):
            raise TypeError("text must be a string or TextDocument")
        fallback_used = self.analyzer is None
        if self.analyzer is None:
            from ..nlp.pipeline import NLPAnalyzer

            analyzer = NLPAnalyzer.rule_based()
        else:
            analyzer = self.analyzer

        # UtterancePlanはTTS制限より前の論理契約なので、対応するNLPAnalyzerでは未分割の
        # plan用経路を優先する。旧/外部 analyzer は従来の ``analyze_text`` だけを実装して
        # いる可能性があるため、ここで互換フォールバックする。
        analyze_for_plan = getattr(analyzer, "analyze_for_plan", None)
        analyze = analyze_for_plan if callable(analyze_for_plan) else analyzer.analyze_text
        fallback_used = self.analyzer is None
        backend = getattr(getattr(analyzer, "scene_analyzer", None), "_backend", None)
        scope = getattr(backend, "ensure_job_scope", None)
        async with scope("utterance-plan") if callable(scope) else nullcontext():
            analyzed = await analyze(
                text,
                known_characters=known_characters,
                character_db=character_db,
            )
        plan = self.build(
            analyzed,
            source=source or (text.original if isinstance(text, TextDocument) else text),
            config=config,
            source_kind=source_kind,
            **kwargs,
        )
        document = getattr(analyzed, "document", None)
        if isinstance(document, TextDocument):
            # URLが同じでも本文・読み・分析結果が変われば新しいidentityを作る。
            semantic = {
                "source": source,
                "document": document.to_dict(),
                "utterances": [u.to_dict() for u in plan.utterances],
                "analysis": analyzer.settings.model_dump(mode="json"),
                "rules_version": "speech-quality-2",
                "analysis_backend": backend.analysis_identity()
                if callable(getattr(backend, "analysis_identity", None))
                else {},
                "config": core_identity.config_fingerprint(config or {}),
            }
            plan = replace(
                plan,
                source_documents=(document,),
                source_fingerprint=core_identity.fingerprint(document.original, length=None),
                config_fingerprint=core_identity.fingerprint(semantic, length=None),
                plan_id=kwargs.get("plan_id") or "plan_" + core_identity.fingerprint(semantic),
            )
        scene_analyzer = getattr(analyzer, "scene_analyzer", None)
        fallback_used = fallback_used or bool(getattr(scene_analyzer, "fallback_used", False))
        if fallback_used and not any(item.code == "NLP_FALLBACK" for item in plan.warnings):
            plan = plan.with_warning(
                PlanWarning(
                    code="NLP_FALLBACK",
                    message="rule-based values were used without LLM scene analysis",
                    severity="info",
                )
            )
        return plan

    async def plan_text(self, text: str, **kwargs: Any) -> UtterancePlan:
        return await self.from_text(text, **kwargs)

    async def create_from_text(self, text: str, **kwargs: Any) -> UtterancePlan:
        return await self.from_text(text, **kwargs)

    @classmethod
    def from_batch_manifest(
        cls,
        manifest: "BatchManifest",
        *,
        config: object = None,
        merge_adjacent: bool = True,
    ) -> UtterancePlan:
        """旧 BatchManifest (2-pass 分析結果) を plan へ変換する。"""

        return cls().build(
            manifest,
            source=_value(manifest, "source_url", default=None),
            config=config,
            plan_id=_value(manifest, "plan_id", default=None),
            source_fingerprint=_value(manifest, "source_fingerprint", default=None),
            config_fingerprint=_value(manifest, "config_fingerprint", default=None),
            created_at=_value(manifest, "created_at", default=None),
            merge_adjacent=merge_adjacent,
        )

    from_manifest = from_batch_manifest


class ReaderPlanningAdapter:
    """Reader 用の薄い adapter。``UtterancePlanner`` と同じ変換を使う。"""

    def __init__(self, planner: UtterancePlanner | None = None):
        self.planner = planner or UtterancePlanner()

    async def plan(self, text: str, **kwargs: Any) -> UtterancePlan:
        return await self.planner.from_text(text, **kwargs)

    create_plan = plan

    def plan_sync(self, text: str, **kwargs: Any) -> UtterancePlan:
        return asyncio.run(self.plan(text, **kwargs))

    def from_results(self, results: object, **kwargs: Any) -> UtterancePlan:
        """既に解析済みのReader結果を同じ変換境界へ通す."""

        return self.planner.build(results, **kwargs)

    to_plan = from_results


class ApiPlanningAdapter(ReaderPlanningAdapter):
    """公開 API (TextAnalyzer) 用の命名 adapter。"""

    async def plan(self, value: object, **kwargs: Any) -> UtterancePlan:
        """本文または ``AnalysisResult`` 列を plan 化する.

        APIの新経路は本文を ``TextAnalyzer.plan`` へ渡す一方、旧SDK callers は
        ``TextAnalyzer.analyze`` の結果を保存してから変換することがある。両方を同じ
        ``UtterancePlanner`` に集約し、後者で文字列列を誤って再解析しないようにする。
        """

        if isinstance(value, (str, TextDocument)):
            return await self.planner.from_text(value, **kwargs)
        return self.planner.build(value, **kwargs)

    create_plan = plan

    def from_results(self, results: object, **kwargs: Any) -> UtterancePlan:
        return self.planner.build(results, **kwargs)

    to_plan = from_results


class BatchPlanningAdapter:
    """旧 Batch manifest の読み取り adapter。"""

    def __init__(self, planner: UtterancePlanner | None = None):
        self.planner = planner or UtterancePlanner()

    def plan(self, manifest: "BatchManifest", **kwargs: Any) -> UtterancePlan:
        # ``UtterancePlanner.from_batch_manifest`` remains a classmethod for legacy callers.
        # Invoke the configured planner instance here so Reader/API/Batch share the same
        # fallback policy and warning metadata instead of silently constructing a fresh planner.
        return self.planner.build(
            manifest,
            source=_value(manifest, "source_url", default=None),
            config=kwargs.get("config"),
            plan_id=_value(manifest, "plan_id", default=None),
            source_fingerprint=_value(manifest, "source_fingerprint", default=None),
            config_fingerprint=_value(manifest, "config_fingerprint", default=None),
            created_at=_value(manifest, "created_at", default=None),
            merge_adjacent=kwargs.get("merge_adjacent", True),
        )

    create_plan = plan
    from_manifest = plan
    to_utterance_plan = plan


# Descriptive aliases ease migration from experiments that called this a builder/service.
PlanningService = UtterancePlanner
UtterancePlanBuilder = UtterancePlanner
NLPPlanAdapter = UtterancePlanner


async def plan_text(text: str, **kwargs: Any) -> UtterancePlan:
    """デフォルト planner で本文を plan 化する便利関数。"""

    return await UtterancePlanner().from_text(text, **kwargs)


def plan_from_results(results: object, **kwargs: Any) -> UtterancePlan:
    return UtterancePlanner().build(results, **kwargs)


def plan_from_batch_manifest(manifest: "BatchManifest", **kwargs: Any) -> UtterancePlan:
    return UtterancePlanner.from_batch_manifest(manifest, **kwargs)


# Compatibility names used by early migration callers.
build_plan = plan_from_results
plan_from_nlp = plan_from_results


__all__ = [
    "ApiPlanningAdapter",
    "BatchPlanningAdapter",
    "NLPPlanAdapter",
    "PlanningContext",
    "PlanningService",
    "ReaderPlanningAdapter",
    "UtterancePlanBuilder",
    "UtterancePlanner",
    "build_plan",
    "plan_from_batch_manifest",
    "plan_from_nlp",
    "plan_from_results",
    "plan_text",
]
