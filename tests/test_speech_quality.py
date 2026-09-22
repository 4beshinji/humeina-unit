"""SQ-001〜005: 原文・読み・入口の一貫性と、物理要求上限の回帰検証。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from yomiage.adapters.persistence.pronunciation import DictionaryEntry, PronunciationDictionary
from yomiage.application.planning import UtterancePlanner
from yomiage.core.plan import UtterancePlan
from yomiage.core.text import ReadingSpan, TextDocument
from yomiage.llm import LLMError, LLMErrorCategory
from yomiage.llm.budgeting import TokenBudget, estimate_tokens
from yomiage.nlp.classifier import SegmentType, TextSegment
from yomiage.nlp.pipeline import NLPAnalyzer
from yomiage.nlp.scene_analyzer import SceneAnalyzer
from yomiage.nlp.text_processor import TextProcessor, extract_html_document
from yomiage.settings import AnalysisSettings

CASES = json.loads((Path(__file__).parent / "fixtures/speech_quality/cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_quality_corpus_without_llm(case):
    original = extract_html_document(case["html"]) if "html" in case else case["text"]
    backend = AsyncMock()
    settings = AnalysisSettings(content_kind=case["kind"], llm_policy="off")
    plan = await UtterancePlanner(NLPAnalyzer(llm=backend, analysis_settings=settings)).from_text(
        original
    )
    if "spoken" in case:
        assert "".join(u.synthesis_text for u in plan.utterances) == case["spoken"]
    if "speakers" in case:
        assert [u.speaker_id for u in plan.utterances] == case["speakers"]
    if "original" in case:
        assert plan.source_documents[0].original == case["original"]
    for u in plan.utterances:
        assert (
            plan.source_documents[0].original[u.source_ref.start : u.source_ref.end]
            == u.text_content.original
        )
    assert UtterancePlan.from_json(plan.to_json()) == plan
    backend.generate_json.assert_not_called()
    backend.is_available.assert_not_called()


def test_dictionary_priority_restart_and_positions(tmp_path):
    common, work = tmp_path / "common.yaml", tmp_path / "work.yaml"
    PronunciationDictionary.save(common, [DictionaryEntry("a", "小鳥遊", "ことりあそび")])
    PronunciationDictionary.save(work, [DictionaryEntry("b", "小鳥遊", "たかなし", "2")])
    document = TextDocument.plain("小鳥遊と小鳥遊").with_readings(
        [
            ReadingSpan(4, 7, "小鳥遊", "ことりゆう", "ruby"),
            ReadingSpan(0, 3, "小鳥遊", "とりさん", "explicit"),
        ]
    )
    result = PronunciationDictionary(common, work).apply(document)
    assert result.synthesis_text == "とりさんとことりゆう"
    assert len(result.conflicts) == 4
    assert PronunciationDictionary(common, work).apply(document) == result
    assert common.exists() and work.exists()


def test_dictionary_overlap_case_context_and_atomic_validation(tmp_path):
    path = tmp_path / "dict.yaml"
    entries = [
        DictionaryEntry("short", "日本", "にほん"),
        DictionaryEntry("long", "日本橋", "にっぽんばし"),
        DictionaryEntry("case", "api", "アピ", before="x ", after="。"),
    ]
    PronunciationDictionary.save(path, entries)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        PronunciationDictionary.save(path, [entries[0], entries[0]])
    assert path.read_bytes() == before
    result = PronunciationDictionary(path).apply(TextDocument.plain("日本橋 API x api。api"))
    assert result.synthesis_text == "にっぽんばし API x アピ。api"
    assert len(result.conflicts) == 1
    proposed = tmp_path / "proposed.yaml"
    PronunciationDictionary.save(proposed, entries, confirmed=False)
    with pytest.raises(ValueError, match="unconfirmed"):
        PronunciationDictionary(proposed).apply(TextDocument.plain("日本橋"))


def test_invalid_reading_ranges_and_normalization():
    with pytest.raises(ValueError, match="surface"):
        TextDocument.plain("小鳥遊").with_readings([ReadingSpan(0, 2, "小鳥遊", "たかなし")])
    with pytest.raises(ValueError, match="overlap"):
        TextDocument.plain(
            "日本橋",
            (ReadingSpan(0, 2, "日本", "にほん"), ReadingSpan(0, 3, "日本橋", "にほんばし")),
        )
    document = TextProcessor().process_document(
        TextDocument.plain("㍿ｶﾞ小鳥遊").with_readings(
            [
                ReadingSpan(3, 6, "小鳥遊", "たかなし"),
            ]
        )
    )
    assert document.text == "株式会社ガ小鳥遊"
    assert document.source_map[:4] == ((0, 1),) * 4
    assert document.source_map[4] == (1, 3)
    assert document.synthesis_text == "株式会社ガたかなし"


class CountingBackend:
    def __init__(self, cap=2048):
        self.cap = cap
        self.requests = []

    def effective_output_limit(self, task, requested):
        return min(requested, self.cap)

    async def generate_json(self, prompt, **kwargs):
        self.requests.append((prompt, kwargs))
        return [
            {"segment_id": int(index), "speaker": None, "status": "deferred"}
            for index in re.findall(r"^\[(\d+)\]", prompt, re.M)
        ]


def segments(count):
    return [TextSegment(f"段落{i}。", SegmentType.NARRATION, i) for i in range(count)]


async def test_500_paragraphs_and_25_outputs_fit_effective_limit():
    backend = CountingBackend()
    scene = SceneAnalyzer(backend)
    result = await scene.analyze_batch(segments(500))
    assert len(result) == 500
    assert len(backend.requests) == 32  # 2048に入るのは16対象まで。
    for prompt, kwargs in backend.requests:
        count = len(re.findall(r"^\[(\d+)\]", prompt, re.M))
        assert kwargs["max_tokens"] == 512 + 96 * count <= 2048
        assert estimate_tokens(prompt + kwargs["system"]) + kwargs["max_tokens"] <= 32768 * 0.8


async def test_large_context_and_single_long_utterance():
    backend = CountingBackend()
    scene = SceneAnalyzer(backend, budget=TokenBudget(4096, 0.8, 25))
    text = "「" + "長い台詞。" * 1500 + "」"
    result = await scene.analyze_batch(
        [TextSegment(text, SegmentType.DIALOGUE, 77)],
        known_characters=["人物" + str(i) for i in range(50)],
        context="前後文脈" * 40,
    )
    assert len(result) == 1 and result[0].index == 77 and result[0].text == text
    assert len(backend.requests) > 1
    for prompt, kwargs in backend.requests:
        assert estimate_tokens(kwargs["system"] + prompt) + kwargs["max_tokens"] <= 4096 * 0.8
    backend.requests.clear()
    await scene.analyze_batch(segments(3), known_characters=["人物" * 4000])
    assert backend.requests == [] and scene.fallback_used


async def test_retry_only_invalid_ids_and_keep_success_on_budget():
    class Backend(CountingBackend):
        async def generate_json(self, prompt, **kwargs):
            self.requests.append((prompt, kwargs))
            if len(self.requests) == 1:
                return [
                    {"segment_id": 0, "speaker": "太郎"},
                    {"segment_id": 1},
                    {"segment_id": 1},
                    {"segment_id": 900},
                    {"segment_id": 2, "emotion": "invalid"},
                ]
            raise LLMError(LLMErrorCategory.BUDGET, "exhausted")

    backend = Backend()
    result = await SceneAnalyzer(backend).analyze_batch(segments(3))
    assert result[0].speaker == "太郎"
    assert [s.speaker for s in result[1:]] == [None, None]
    assert "[0]" not in backend.requests[1][0]
    assert "[1]" in backend.requests[1][0] and "[2]" in backend.requests[1][0]
    assert len(backend.requests) == 2


async def test_context_overflow_is_finite_and_retains_results():
    class Backend(CountingBackend):
        async def generate_json(self, prompt, **kwargs):
            ids = re.findall(r"^\[(\d+)\]", prompt, re.M)
            self.requests.append((prompt, kwargs))
            if len(ids) > 1:
                raise LLMError(LLMErrorCategory.CONTEXT_LENGTH, "overflow")
            return [{"segment_id": int(ids[0]), "speaker": "太郎"}]

    backend = Backend()
    result = await SceneAnalyzer(backend, max_requests=5).analyze_batch(segments(20))
    assert len(backend.requests) == 5
    assert any(r.speaker == "太郎" for r in result)
    assert len(result) == 20


async def test_selective_preserves_fixed_speakers_and_readings():
    backend = CountingBackend()
    nlp = NLPAnalyzer(llm=backend, analysis_settings=AnalysisSettings(llm_policy="selective"))
    plan = await UtterancePlanner(nlp).from_text(
        extract_html_document("太郎は「<ruby>小鳥遊<rt>たかなし</rt></ruby>さん」と言った。")
    )
    assert backend.requests == []
    assert plan.utterances[1].speaker_id == "太郎"
    assert plan.utterances[1].synthesis_text == "「たかなしさん」"
    await UtterancePlanner(nlp).from_text("太郎は「行く」と言った。「誰？」")
    assert len(backend.requests) == 1
    targets = re.findall(r"^\[(\d+)\].*", backend.requests[0][0], re.M)
    assert len(targets) == 1
    assert "誰?" in backend.requests[0][0]


async def test_dictionary_change_creates_new_plan_and_preserves_old(tmp_path):
    path = tmp_path / "dictionary.yaml"
    settings = AnalysisSettings(llm_policy="off", common_dictionary=str(path))

    async def plan():
        return await UtterancePlanner(NLPAnalyzer(analysis_settings=settings)).from_text(
            "小鳥遊", source="https://example.test"
        )

    PronunciationDictionary.save(path, [DictionaryEntry("name", "小鳥遊", "たかなし")])
    first = await plan()
    PronunciationDictionary.save(path, [DictionaryEntry("name", "小鳥遊", "ことりゆう", "2")])
    second = await plan()
    assert first.plan_id != second.plan_id
    assert first.utterances[0].synthesis_text == "たかなし"
    assert second.utterances[0].synthesis_text == "ことりゆう"


def test_schema_one_does_not_invent_original():
    payload = {
        "schema_version": 1,
        "plan_id": "old-plan",
        "source_fingerprint": "old-source",
        "config_fingerprint": "old-config",
        "created_at": "2026-01-01T00:00:00Z",
        "utterances": [
            {"id": "old", "order": 17, "text": "保存済み本文", "segment_type": "narration"}
        ],
        "warnings": [],
    }
    migrated = UtterancePlan.from_dict(payload)
    assert migrated.schema_version == 2
    assert migrated.plan_id == "old-plan" and migrated.utterances[0].order == 17
    assert migrated.utterances[0].text_content is None and migrated.source_documents == ()
    assert any(w.code == "ORIGINAL_UNAVAILABLE" for w in migrated.warnings)
    assert payload["schema_version"] == 1


async def test_reading_proposals_are_positioned_validated_and_unconfirmed(tmp_path):
    class Backend(CountingBackend):
        async def generate_json(self, prompt, **kwargs):
            self.requests.append((prompt, kwargs))
            if kwargs.get("task") == "romanization":
                return [
                    {"id": 0, "start": 0, "end": 3, "status": "resolved", "reading": "たかなし"},
                    {"id": 1, "start": 4, "end": 7, "status": "resolved", "reading": "<bad>"},
                ]
            return []

    backend = Backend()
    settings = AnalysisSettings(
        llm_policy="selective",
        content_kind="plain",
        reading_proposals=str(tmp_path / "proposals.yaml"),
    )
    document = replace(TextDocument.plain("小鳥遊と小鳥遊"), reading_targets=((0, 3), (4, 7)))
    plan = await UtterancePlanner(NLPAnalyzer(llm=backend, analysis_settings=settings)).from_text(
        document
    )
    assert plan.utterances[0].synthesis_text == "たかなしと小鳥遊"
    assert plan.utterances[0].text_content.readings[0].origin == "llm"
    assert len(backend.requests) == 1
    with pytest.raises(ValueError, match="unconfirmed"):
        PronunciationDictionary.load(tmp_path / "proposals.yaml")


async def test_off_constructs_no_llm_at_bootstrap_cli_sdk_and_batch(monkeypatch, tmp_path):
    from yomiage import bootstrap
    from yomiage.api.analyzer import TextAnalyzer
    from yomiage.api.config import AnalyzerConfig, PipelineConfig, TTSEngineConfig
    from yomiage.api.pipeline import Pipeline
    from yomiage.application.health import HealthChecker
    from yomiage.application.synthesis import SynthesisService
    from yomiage.batch.engine import BatchEngine
    from yomiage.interfaces.cli import composition

    from .test_reader_plan import FakeProvider, FakeTTSManager

    def forbidden(*args, **kwargs):
        raise AssertionError("off mode constructed an LLM")

    monkeypatch.setattr(bootstrap, "create_llm_service", forbidden)
    monkeypatch.setattr("yomiage.llm.factory.create_llm_service", forbidden)
    monkeypatch.setattr("yomiage.batch.engine.create_llm_service", forbidden)
    monkeypatch.setattr("yomiage.api.pipeline.create_llm_backend", forbidden)
    monkeypatch.setattr("yomiage.api.analyzer.create_llm_backend", forbidden)
    manager = FakeTTSManager(FakeProvider())
    service = SynthesisService(manager.primary)
    manager.service = service
    config = {
        "analysis": {"llm_policy": "off", "content_kind": "plain"},
        "tts": {"primary_provider": "voicevox"},
        "batch": {"output_dir": str(tmp_path)},
    }
    monkeypatch.setattr(bootstrap, "create_tts_manager", lambda *a, **k: manager)
    monkeypatch.setattr(
        "yomiage.interfaces.cli.common._compat_helper", lambda name: lambda *a, **k: manager
    )
    reader = bootstrap.create_reading_engine(config)
    cli_reader = composition.create_reading_engine(config)
    assert reader.llm is None and cli_reader.llm is None
    batch = BatchEngine(config, synthesis_service=service)
    assert batch.llm is None
    sdk_config = PipelineConfig(
        tts=TTSEngineConfig(engine="voicevox"),
        analyzer=AnalyzerConfig(analysis=AnalysisSettings(llm_policy="off")),
    )
    sdk = Pipeline(sdk_config, synthesis_service=service)
    TextAnalyzer.from_config(sdk_config.analyzer)
    await sdk.process("テスト。")
    probe = AsyncMock()
    health = HealthChecker(config=config, llm=probe)
    check = await health._llm_check()
    assert check.healthy
    probe.is_available.assert_not_called()
    await sdk.close()
    await service.close()


async def test_reader_batch_sdk_share_structure_readings_and_parameters(tmp_path, monkeypatch):
    from yomiage.api.config import AnalyzerConfig, PipelineConfig, TTSEngineConfig
    from yomiage.api.pipeline import Pipeline
    from yomiage.application.synthesis import SynthesisRequest, SynthesisService
    from yomiage.batch import analyzer as batch_module
    from yomiage.batch.analyzer import BatchAnalyzer
    from yomiage.batch.engine import BatchEngine
    from yomiage.reader.character_db import CharacterDB
    from yomiage.reader.engine import ReadingEngine
    from yomiage.sources.base import Chapter, ChapterInfo

    from .test_reader_plan import FakeProvider, FakeTTSManager

    document = extract_html_document(
        "太郎は「<ruby>小鳥遊<rt>たかなし</rt></ruby>さん。おはよう」と言った。"
    )
    settings = AnalysisSettings(llm_policy="off")

    class Source:
        async def get_table_of_contents(self, url):
            return [ChapterInfo("test", url, 0)]

        async def fetch_chapter(self, url):
            return Chapter("test", document.text, "", url, 0, document=document)

    monkeypatch.setattr(batch_module.registry, "resolve", lambda *a, **k: Source())
    monkeypatch.setattr(
        batch_module, "CharacterDB", lambda work_id: CharacterDB(work_id, persist=False)
    )
    config = {
        "analysis": settings.model_dump(),
        "tts": {"primary_provider": "voicevox"},
        "batch": {"output_dir": str(tmp_path)},
    }
    provider = FakeProvider()
    service = SynthesisService(provider)
    reader = ReadingEngine(
        FakeTTSManager(provider),
        synthesis_service=service,
        analysis_settings=settings,
        data_dir=tmp_path,
    )
    reader._character_db = CharacterDB("test", persist=False)
    reader_plan = await reader._plan_text(document)
    manifest = await BatchAnalyzer(None, analysis_settings=settings).analyze(
        "https://example.test/", output_dir=str(tmp_path)
    )
    batch_plan = manifest.to_utterance_plan(merge_adjacent=False)
    sdk = Pipeline(
        PipelineConfig(
            tts=TTSEngineConfig(engine="voicevox"), analyzer=AnalyzerConfig(analysis=settings)
        ),
        synthesis_service=service,
    )
    await sdk.process(document)
    plans = [reader_plan, batch_plan, sdk.last_plan]

    def structure(plan):
        return [
            (u.text, u.speaker_id, u.segment_type, u.synthesis_text, u.text_content.to_dict())
            for u in plan.utterances
        ]

    assert structure(plans[0]) == structure(plans[1]) == structure(plans[2])
    batch = BatchEngine(config, mode="fake", synthesis_service=service)
    db = CharacterDB("empty", persist=False)
    item = reader_plan.utterances[1]
    context = reader.param_mapper.context_for(item, db)
    await service.synthesize(
        replace(SynthesisRequest.from_utterance(item), parameter_context=context)
    )
    reader_request = provider.calls[-1]
    request = batch._request_for_entry(
        manifest.sentences[1],
        batch_plan.utterances[1],
        character_db=db,
        synthesizer=None,
        work_dir=tmp_path,
        manifest=manifest,
        service=service,
    )
    await service.synthesize(replace(request, destination=None, work_id=None, run_id=None))
    batch_request = provider.calls[-1]
    assert reader_request.text == batch_request.text == "「たかなしさん。おはよう」"
    assert reader_request.voice_intent == batch_request.voice_intent
    assert reader_request.resolved_parameters == batch_request.resolved_parameters
    await service.close()


async def test_new_manifest_roundtrip_preserves_readings(tmp_path):
    from yomiage.batch.manifest import BatchManifest, SentenceEntry

    document = TextDocument.plain("小鳥遊").with_readings([ReadingSpan(0, 3, "小鳥遊", "たかなし")])
    manifest = BatchManifest(
        "work",
        "title",
        "https://example.test",
        "voicevox",
        sentences=[SentenceEntry(17, "小鳥遊", 0, "narration", text_content=document.to_dict())],
        source_documents=[document.to_dict()],
        plan_id="new-plan",
        run_id="new-run",
    )
    manifest.save(tmp_path)
    restored = BatchManifest.load(tmp_path, "work", run_id="new-run")
    assert restored.schema_version == 3
    assert restored.sentences[0].index == 17
    assert restored.to_utterance_plan().utterances[0].synthesis_text == "たかなし"
    assert restored.source_documents == manifest.source_documents


async def test_ex_voice_runs_after_plan_playback(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from yomiage.application.synthesis import SynthesisService
    from yomiage.reader.engine import ReadingEngine

    from .test_audio_validation import wav_bytes
    from .test_reader_plan import FakeProvider, FakeTTSManager

    clip = tmp_path / "clip.wav"
    clip.write_bytes(wav_bytes())
    ex = SimpleNamespace(
        reset_chapter=lambda: None,
        analyze_window=AsyncMock(),
        pop_clips_for=lambda index: [SimpleNamespace(path=clip)],
    )
    played = AsyncMock()
    monkeypatch.setattr("yomiage.reader.engine.play_wav", played)

    class Queue:
        async def play_plan(self, plan, **kwargs):
            for u in plan.utterances:
                await kwargs["on_played"](u, None)
            return []

    provider = FakeProvider()
    service = SynthesisService(provider)
    reader = ReadingEngine(
        FakeTTSManager(provider),
        synthesis_service=service,
        playback_queue=Queue(),
        ex_voice=ex,
        data_dir=tmp_path,
    )
    plan = await reader._plan_text("こんにちは。")
    await reader._read_plan_for_chapter(plan, chapter=None, chapter_url=None)
    ex.analyze_window.assert_awaited_once()
    played.assert_awaited_once()
    await service.close()


async def test_concurrent_plans_keep_their_original_documents():
    import asyncio

    class Backend(CountingBackend):
        async def generate_json(self, prompt, **kwargs):
            await asyncio.sleep(0.005 if "最初" in prompt else 0)
            return await super().generate_json(prompt, **kwargs)

    planner = UtterancePlanner(NLPAnalyzer(llm=Backend()))
    first, second = await asyncio.gather(
        planner.from_text("最初の本文"), planner.from_text("次の本文")
    )
    assert first.source_documents[0].original == "最初の本文"
    assert second.source_documents[0].original == "次の本文"
    assert first.plan_id != second.plan_id


def test_user_dictionary_overrides_builtin_and_paths_follow_yaml(tmp_path):
    from yomiage.config import load_config

    common = tmp_path / "words.yaml"
    PronunciationDictionary.save(common, [DictionaryEntry("zz-user", "AI", "アイ")])
    document = TextProcessor().process_document("AI")
    assert PronunciationDictionary(common).apply(document).synthesis_text == "アイ"
    config_dir = tmp_path / "settings"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text(
        'analysis:\n  llm_policy: "off"\n  common_dictionary: ../words.yaml\n'
    )
    config = load_config(config_dir)
    assert config["analysis"]["common_dictionary"] == str(common)


def test_same_profile_uses_same_canonical_context_for_all_callers():
    from yomiage.application.voice_parameters import (
        resolve_voice_parameters,
        translate_provider_parameters,
    )
    from yomiage.core.models import Utterance, VoiceIntent
    from yomiage.reader.param_mapper import ParamMapper
    from yomiage.tools.voicepeak_profile import VoicepeakVoiceProfile

    profile = VoicepeakVoiceProfile.create_default("narrator", "narrator")
    item = Utterance("one", 0, "話す", "dialogue", voice_intent=VoiceIntent(archetype="male_adult"))
    mapper = ParamMapper({"scenes": {"daily": {"speed": 1.1}}})
    context = mapper.context_for(item, voice_profile=profile, provider="voicepeak")
    native_profile = profile.compute_params("male_adult", emotion="neutral", intensity=0.5)
    assert context.profile["speed"] == native_profile["speed"] / 100
    assert context.profile["pitch"] == native_profile["pitch"] / 300
    resolved = resolve_voice_parameters(
        item.voice_intent,
        profile=context.profile,
        scene=context.scene,
        emotion=context.emotion,
        overrides=context.overrides,
    )
    native = translate_provider_parameters("voicepeak", resolved).values
    assert native["speed"] == round(native_profile["speed"] * 1.1)


def test_builtin_readings_after_nfkc_keep_original_positions():
    document = TextProcessor().process_document("ＡＩ API.")
    assert document.synthesis_text == "エーアイ エーピーアイ."
    assert document.readings[0].surface == "ＡＩ"
    assert (document.readings[0].start, document.readings[0].end) == (0, 2)


def test_audio_cache_requires_known_provider_version_between_instances():
    from yomiage.adapters.tts.legacy import LegacyTTSProviderAdapter
    from yomiage.tts.factory import create_provider_from_dict

    first = LegacyTTSProviderAdapter(create_provider_from_dict("voicevox", {}))
    second = LegacyTTSProviderAdapter(create_provider_from_dict("voicevox", {}))
    assert first.cache_version != second.cache_version
    assert LegacyTTSProviderAdapter(first.provider).cache_version == first.cache_version
    configured = {"cache_version": "engine-bank-1"}
    versioned = LegacyTTSProviderAdapter(create_provider_from_dict("voicevox", configured))
    restarted = LegacyTTSProviderAdapter(create_provider_from_dict("voicevox", configured))
    assert versioned.cache_version == restarted.cache_version == "engine-bank-1"
