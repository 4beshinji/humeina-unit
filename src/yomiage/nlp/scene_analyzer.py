"""Scene and emotion analysis via SLM (Ollama)."""

from dataclasses import dataclass, field

from loguru import logger

from ..llm import LLMBackend
from .classifier import SegmentType, TextSegment

ANALYSIS_SYSTEM_PROMPT = """\
あなたは小説テキスト分析器です。テキストセグメントを分析し、各セグメントの\
話者・シーン・感情を判定してください。JSON形式で回答してください。"""

ANALYSIS_PROMPT_TEMPLATE = """\
既知のキャラクター: {characters}

以下のテキストセグメントを分析してください:
{segments}

各セグメントについてJSON配列で回答:
[{{"segment_id": 0, "speaker": "キャラ名" or null, \
"new_character": {{"name": "名前", "gender": "male/female/unknown", \
"age_group": "child/teen/young_adult/adult/elder"}} or null, \
"scene": "daily/battle/romance/tense/comedy/sad/horror", \
"emotion": "neutral/happy/angry/sad/surprised/scared/gentle", \
"intensity": 0.0-1.0}}]"""

ANALYSIS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"segment_id": {"type": "integer"}},
        "required": ["segment_id"],
    },
}


@dataclass
class AnalyzedSegment:
    """SLM分析済みセグメント."""

    text: str
    type: SegmentType
    index: int
    speaker: str | None = None
    scene: str = "daily"
    emotion: str = "neutral"
    intensity: float = 0.5
    new_character: dict | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_segment(cls, seg: TextSegment, **kwargs) -> "AnalyzedSegment":
        return cls(
            text=seg.text,
            type=seg.type,
            index=seg.index,
            **kwargs,
        )


class SceneAnalyzer:
    """SLMによるシーン・感情分析."""

    def __init__(self, backend: LLMBackend):
        self._backend = backend
        # planning adapter が「解析は完了したが rule-based fallback を使った」ことを
        # warningへ反映できるよう、インスタンスの生存期間中に一度でもfallbackしたかを
        #保持する。NLPAnalyzerが複数チャンクを順に処理しても、途中の失敗を最後の結果で
        #上書きしない。
        self._fallback_used = False

    @property
    def fallback_used(self) -> bool:
        """LLM unavailable/失敗時の既定分析を一度でも使用したか."""

        return self._fallback_used

    async def analyze_batch(
        self,
        segments: list[TextSegment],
        known_characters: list[str] | None = None,
    ) -> list[AnalyzedSegment]:
        """セグメントバッチをSLMで分析."""
        if not segments:
            return []

        # セグメントテキストを準備
        seg_texts = []
        for i, seg in enumerate(segments):
            if seg.type == SegmentType.SCENE_BREAK:
                continue
            seg_texts.append(f"[{i}] ({seg.type.value}) {seg.text}")

        if not seg_texts:
            return self._default_analysis(segments)

        chars_str = ", ".join(known_characters) if known_characters else "なし"
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            characters=chars_str,
            segments="\n".join(seg_texts),
        )

        try:
            result = await self._backend.generate_json(
                prompt,
                system=ANALYSIS_SYSTEM_PROMPT,
                task="scene_analysis",
                json_schema=ANALYSIS_SCHEMA,
            )
        except Exception as e:
            self._fallback_used = True
            logger.warning(f"SLM analysis failed: {e}")
            return self._default_analysis(segments)

        return self._merge_results(segments, result)

    def _merge_results(
        self, segments: list[TextSegment], slm_results: list | dict
    ) -> list[AnalyzedSegment]:
        """SLM結果をセグメントにマージ."""
        analyzed = []

        # SLM結果をインデックスでマップ
        result_map: dict[int, dict] = {}
        if isinstance(slm_results, list):
            for r in slm_results:
                if isinstance(r, dict) and "segment_id" in r:
                    result_map[r["segment_id"]] = r

        for seg in segments:
            r = result_map.get(seg.index, {})

            speaker = r.get("speaker")
            # ルールベース候補が存在する場合はそちらを優先（SLMは補正として使う）
            if not speaker and seg.speaker_candidates:
                speaker = seg.speaker_candidates[0]

            analyzed.append(
                AnalyzedSegment.from_segment(
                    seg,
                    speaker=speaker,
                    scene=r.get("scene", "daily"),
                    emotion=r.get("emotion", "neutral"),
                    intensity=r.get("intensity", 0.5),
                    new_character=r.get("new_character"),
                )
            )

        return analyzed

    def _default_analysis(self, segments: list[TextSegment]) -> list[AnalyzedSegment]:
        """SLM不可時のデフォルト分析."""
        return [
            AnalyzedSegment.from_segment(
                seg,
                speaker=seg.speaker_candidates[0] if seg.speaker_candidates else None,
            )
            for seg in segments
        ]
