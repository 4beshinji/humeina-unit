"""DOM-002 cross-entry planning contract tests."""

from __future__ import annotations

from yomiage.api.analyzer import TextAnalyzer
from yomiage.application.planning import (
    BatchPlanningAdapter,
    ReaderPlanningAdapter,
    UtterancePlanner,
)
from yomiage.batch.manifest import BatchManifest, SentenceEntry
from yomiage.nlp.llm_backend import LLMBackend
from yomiage.nlp.pipeline import NLPAnalyzer


class DeterministicLLM(LLMBackend):
    """シーン結果を固定返却する fake LLM（外部通信なし）。"""

    def __init__(self, *, available: bool = True):
        self.available = available

    async def generate(self, prompt: str, **kwargs: object) -> str:
        return ""

    async def generate_json(self, prompt: str, **kwargs: object) -> list[dict]:
        # NLP classifier の segment index に対して固定値を返す。
        return [
            {
                "segment_id": 0,
                "speaker": None,
                "scene": "daily",
                "emotion": "neutral",
                "intensity": 0.1,
            },
            {
                "segment_id": 1,
                "speaker": "太郎",
                "scene": "battle",
                "emotion": "angry",
                "intensity": 0.9,
            },
            {
                "segment_id": 2,
                "speaker": None,
                "scene": "daily",
                "emotion": "neutral",
                "intensity": 0.1,
            },
            {
                "segment_id": 3,
                "speaker": "花子",
                "scene": "tense",
                "emotion": "sad",
                "intensity": 0.7,
            },
            {
                "segment_id": 4,
                "speaker": None,
                "scene": "daily",
                "emotion": "neutral",
                "intensity": 0.1,
            },
        ]

    async def is_available(self) -> bool:
        return self.available


def _speakers(plan):
    return [utterance.speaker_id for utterance in plan.utterances]


async def test_reader_and_api_have_same_order_and_speakers() -> None:
    text = "空は青かった。太郎は「行くぞ」と言った。花子は「待って」と答えた。"
    analyzer = NLPAnalyzer(llm=DeterministicLLM())
    reader_plan = await ReaderPlanningAdapter(UtterancePlanner(analyzer)).plan(text)
    api_plan = await TextAnalyzer(llm=DeterministicLLM()).plan(text)

    assert [(u.order, u.speaker_id) for u in reader_plan.utterances] == [
        (u.order, u.speaker_id) for u in api_plan.utterances
    ]
    assert _speakers(reader_plan) == [None, "太郎", None, "花子", None]
    assert [u.emotion.value for u in reader_plan.utterances] == [
        "neutral",
        "angry",
        "neutral",
        "sad",
        "neutral",
    ]


async def test_llm_unavailable_still_builds_rule_based_plan() -> None:
    planner = UtterancePlanner(NLPAnalyzer(llm=DeterministicLLM(available=False)))
    plan = await planner.from_text("太郎は「こんにちは」と言った。")

    assert plan.utterances
    assert plan.utterances[0].speaker_id is None
    assert plan.utterances[1].speaker_id == "太郎"
    assert any(warning.code == "NLP_FALLBACK" for warning in plan.warnings)


async def test_plan_keeps_long_dialogue_together_before_tts_split() -> None:
    text = '太郎は「' + ("あ" * 500) + '」と言った。'
    # max_chunk_chars is intentionally much smaller than the dialogue.  It belongs to the
    # synthesis stage and must not turn the tail of a quoted segment into narration.
    planner = UtterancePlanner(NLPAnalyzer.rule_based(max_chunk_chars=20))

    plan = await planner.from_text(text)

    dialogue = [
        utterance
        for utterance in plan.utterances
        if utterance.segment_type.value == "dialogue"
    ]
    assert len(dialogue) == 1
    assert dialogue[0].speaker_id == "太郎"
    assert dialogue[0].text == '「' + ("あ" * 500) + '」'


async def test_api_adapter_accepts_legacy_analysis_results() -> None:
    text = "太郎は「行くぞ」と言った。花子は「待って」と答えた。"
    analyzer = TextAnalyzer.rule_based()

    results = await analyzer.analyze(text)
    from_results = analyzer.plan_results(results)
    from_text = await analyzer.plan(text)

    assert [(item.order, item.speaker_id) for item in from_results.utterances] == [
        (item.order, item.speaker_id) for item in from_text.utterances
    ]


def test_batch_two_pass_manifest_adapter_preserves_order_and_speakers() -> None:
    manifest = BatchManifest(
        work_id="work-test",
        work_title="test",
        source_url="https://example.test/book/1",
        mode="voicevox",
        analysis_complete=True,
        plan_id="plan-test",
        source_fingerprint="source-test",
        config_fingerprint="config-test",
        sentences=[
            SentenceEntry(
                index=2,
                text="B",
                chapter_index=0,
                segment_type="dialogue",
                speaker="花子",
            ),
            SentenceEntry(index=0, text="地の文", chapter_index=0, segment_type="narration"),
            SentenceEntry(
                index=1,
                text="A",
                chapter_index=0,
                segment_type="dialogue",
                speaker="太郎",
            ),
        ],
    )

    plan = BatchPlanningAdapter().plan(manifest)
    assert [(u.order, u.speaker_id, u.text) for u in plan.utterances] == [
        (0, None, "地の文"),
        (1, "太郎", "A"),
        (2, "花子", "B"),
    ]
    assert plan.plan_id == "plan-test"
    assert plan.source_fingerprint == "source-test"
