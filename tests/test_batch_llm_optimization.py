"""Batch LLM送信量と部分retryの回帰test。"""

from __future__ import annotations

import re

from yomiage.batch import analyzer as analyzer_module
from yomiage.batch.analyzer import BatchAnalyzer
from yomiage.llm import LLMBackend
from yomiage.reader.character_db import CharacterDB
from yomiage.sources.base import Chapter, ChapterInfo


class _CountingLLM(LLMBackend):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **kwargs: object) -> str:
        return ""

    async def generate_json(self, prompt: str, **kwargs: object) -> list[dict]:
        self.prompts.append(prompt)
        ids = [int(value) for value in re.findall(r"^\[(\d+)]", prompt, flags=re.MULTILINE)]
        return [
            {
                "id": index,
                "speaker": "太郎",
                "new_character": (
                    {
                        "name": "太郎",
                        "gender": "male",
                        "age_group": "adult",
                        "personality": "穏やか",
                    }
                    if index == 0
                    else None
                ),
                "scene": "daily",
                "emotion": "neutral",
                "intensity": 0.5,
                "viewpoint": None,
            }
            for index in ids
        ]

    async def is_available(self) -> bool:
        return True


class _Source:
    async def get_table_of_contents(self, _: str) -> list[ChapterInfo]:
        return [ChapterInfo("test", "https://example.com/chapter", 0)]

    async def fetch_chapter(self, url: str) -> Chapter:
        return Chapter(
            "test",
            "太郎は歩いた。太郎は笑った。太郎は帰った。",
            "",
            url,
            0,
            1,
        )


async def test_standard_batch_sends_body_once_without_discovery_pass(
    monkeypatch, tmp_path
) -> None:
    llm = _CountingLLM()
    monkeypatch.setattr(analyzer_module.registry, "resolve", lambda *args, **kwargs: _Source())
    monkeypatch.setattr(
        analyzer_module,
        "CharacterDB",
        lambda work_id: CharacterDB(work_id, data_dir=tmp_path / "characters"),
    )
    analyzer = BatchAnalyzer(
        llm,
        analysis_window_sentences=25,
        context_tokens=32768,
        analysis_strategy="single_pass",
    )

    manifest = await analyzer.analyze(
        "https://example.com/work", mode="voicevox", output_dir=str(tmp_path)
    )

    assert len(llm.prompts) == 1
    assert "新しい登場人物を抽出" not in llm.prompts[0]
    assert len(manifest.sentences) == 1
    assert manifest.sentences[0].text == "太郎は歩いた。太郎は笑った。太郎は帰った。"
    assert manifest.characters["太郎"]["gender"] == "male"
    assert manifest.characters["太郎"]["base_params"]["_archetype"] == "male_adult"


async def test_missing_ids_only_are_retried() -> None:
    class _PartialLLM(_CountingLLM):
        async def generate_json(self, prompt: str, **kwargs: object) -> list[dict]:
            self.prompts.append(prompt)
            ids = [int(value) for value in re.findall(r"^\[(\d+)]", prompt, flags=re.MULTILINE)]
            selected = ids[:1] if len(self.prompts) == 1 else ids
            return [{"id": index, "scene": "daily"} for index in selected]

    llm = _PartialLLM()
    analyzer = BatchAnalyzer(llm)
    char_db = CharacterDB("partial", persist=False)

    result = await analyzer._analyze_detail_resilient(
        ["一文目。", "二文目。", "三文目。"],
        [{"type": "narration"}] * 3,
        char_db,
        "冒頭",
    )

    assert set(result) == {0, 1, 2}
    assert "一文目" in llm.prompts[0]
    assert "一文目" not in llm.prompts[1]
    assert "二文目" in llm.prompts[1]
    assert "三文目" in llm.prompts[1]
