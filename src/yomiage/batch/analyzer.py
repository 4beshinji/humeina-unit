"""Phase A: token予算ベースの原則1-pass全文分析。"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from loguru import logger

from ..core.identity import (
    build_identity,
    fingerprint,
)
from ..llm import LLMBackend
from ..llm.budgeting import TokenBudget
from ..nlp.classifier import SegmentType, TextClassifier, TextSegment
from ..nlp.pipeline import NLPAnalyzer
from ..nlp.scene_analyzer import SceneAnalyzer
from ..nlp.speaker import SpeakerExtractor
from ..nlp.text_processor import TextProcessor
from ..ports.sources import URLPolicy
from ..reader.character_db import CharacterDB
from ..settings import AnalysisSettings
from ..sources import registry
from ..sources.base import ChapterInfo
from .manifest import BatchManifest, ChapterMeta, SentenceEntry

# --- exhaustive互換用人物発見prompt ---

CHAR_DISCOVERY_SYSTEM = """\
あなたは小説テキスト分析器です。テキストから登場人物を抽出してください。JSON形式で回答してください。"""

CHAR_DISCOVERY_PROMPT = """\
既知のキャラクター:
{known_chars}

以下のテキストから新しい登場人物を抽出してください。
既知のキャラクターと同一人物の場合は aliases に追記してください。

テキスト:
{text}

JSON配列で回答（新規キャラクターのみ）:
[{{"name": "正式名", "aliases": ["別名1"], "gender": "male/female/unknown", \
"age_group": "child/teen/young_adult/adult/elder", \
"personality": "性格の短い説明", "role": "主人公/ヒロイン/敵役/脇役/etc"}}]

既知キャラクターの別名を発見した場合:
{{"merge": [{{"known_name": "既知名", "new_alias": "新しい別名"}}]}}"""

# --- 通常1-pass詳細分析prompt ---

DETAIL_ANALYSIS_SYSTEM = """\
あなたは小説テキスト分析器です。各文を詳細に分析してください。JSON形式で回答してください。"""

DETAIL_ANALYSIS_PROMPT = """\
キャラクター一覧: {characters}
前のシーンの状況: {context}

以下の文を分析してください:
{sentences}

各文についてJSON配列で回答:
[{{"id": 0, "speaker": "キャラ名" or null, \
"new_character": {{"name": "名前", "gender": "male/female/unknown", \
"age_group": "child/teen/young_adult/adult/elder", "personality": "短い説明"}} or null, \
"scene": "daily/battle/romance/tense/comedy/sad/horror", \
"emotion": "neutral/happy/angry/sad/surprised/scared/gentle", \
"intensity": 0.0-1.0, \
"viewpoint": "視点キャラ名" or null}}]"""

DETAIL_ANALYSIS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    },
}


class BatchAnalyzer:
    """詳細分析と新規人物発見を同じ応答へ統合する全文分析器。"""

    def __init__(
        self,
        llm: LLMBackend | None,
        text_processor: TextProcessor | None = None,
        classifier: TextClassifier | None = None,
        speaker_extractor: SpeakerExtractor | None = None,
        analysis_window_chars: int = 3000,
        analysis_window_sentences: int = 25,
        context_tokens: int = 32768,
        context_safety_ratio: float = 0.8,
        analysis_strategy: str = "single_pass",
        voice_profile: Any = None,
        url_policy: URLPolicy | None = None,
        analysis_settings: AnalysisSettings | None = None,
    ):
        self.settings = analysis_settings or AnalysisSettings(
            context_tokens=context_tokens,
            context_safety_ratio=context_safety_ratio,
            max_items=analysis_window_sentences,
        )
        self.llm = llm
        self.processor = text_processor or TextProcessor()
        self.classifier = classifier or TextClassifier(self.settings.content_kind)
        self.speaker_extractor = speaker_extractor or SpeakerExtractor()
        self.window_chars = analysis_window_chars
        self.window_sentences = analysis_window_sentences
        self.token_budget = TokenBudget(
            context_tokens=self.settings.context_tokens,
            safety_ratio=self.settings.context_safety_ratio,
            max_items=self.settings.max_items,
        )
        self.analysis_strategy = analysis_strategy
        self.voice_profile = voice_profile
        self.url_policy = url_policy

    async def analyze(
        self,
        url: str,
        mode: str = "voisona",
        output_dir: str | None = None,
        chapter_range: tuple[int, int] | None = None,
    ) -> BatchManifest:
        scope = getattr(self.llm, "ensure_job_scope", None)
        async with scope("batch-analysis") if callable(scope) else nullcontext():
            return await self._analyze_impl(url, mode, output_dir, chapter_range)

    async def _analyze_impl(
        self,
        url: str,
        mode: str = "voisona",
        output_dir: str | None = None,
        chapter_range: tuple[int, int] | None = None,
    ) -> BatchManifest:
        """URL → 全文分析 → BatchManifest を生成."""
        from pathlib import Path

        source = registry.resolve(url, policy=self.url_policy)
        source_kind = type(source).__name__.removesuffix("Source").lower() or "url"
        identity = build_identity(
            url,
            {
                "llm_context_tokens": self.token_budget.context_tokens,
                "llm_context_safety_ratio": self.token_budget.safety_ratio,
                "llm_max_items": self.token_budget.max_items,
                "analysis_strategy": self.analysis_strategy,
                "splitter_max_chars": 500,
                # ``mode`` is intentionally excluded by the identity utility.  Provider changes
                # should reuse this plan while receiving a fresh run directory.
                "mode": mode,
            },
            source_kind=source_kind,
        )
        work_id = identity.work_id

        # 目次取得
        toc = await source.get_table_of_contents(url)
        if not toc:
            # 単一チャプター作品
            chapter = await source.fetch_chapter(url)
            toc = [ChapterInfo(title=chapter.title, url=url, index=0)]

        # チャプター範囲フィルタ
        if chapter_range:
            start, end = chapter_range
            toc = [ch for ch in toc if start <= ch.index + 1 <= end]

        if not toc:
            raise ValueError("No chapters to analyze")

        work_title = toc[0].title if len(toc) == 1 else f"{toc[0].title}..."
        manifest = BatchManifest(
            work_id=work_id,
            work_title=work_title,
            source_url=url,
            mode=mode,
            plan_id=identity.plan_id,
            run_id=identity.run_id,
            job_id=identity.job_id,
            source_fingerprint=identity.source_fingerprint,
            config_fingerprint=identity.config_fingerprint,
        )

        char_db = CharacterDB(work_id)

        logger.info("=== Phase A: single-pass detail and character analysis ===")
        all_chapter_texts: list[tuple[ChapterMeta, str]] = []

        for ch_info in toc:
            logger.info(f"Fetching chapter {ch_info.index + 1}: {ch_info.title}")
            chapter = await source.fetch_chapter(ch_info.url)
            document = chapter.document or self.processor.process_document(chapter.text)
            clean_text = document.text

            if not clean_text:
                logger.warning(f"Empty chapter: {ch_info.title}")
                continue

            ch_meta = ChapterMeta(
                index=ch_info.index,
                title=ch_info.title,
                url=ch_info.url,
                sentence_start=0,
                sentence_end=0,
            )
            all_chapter_texts.append((ch_meta, document))

            # 品質比較用の明示的exhaustiveモードだけ旧人物発見passを行う。
            if self.analysis_strategy == "exhaustive" and self.settings.llm_policy == "full":
                for window in self._split_windows(clean_text, self.window_chars):
                    await self._discover_characters(window, char_db)

        global_idx = 0
        scene = (
            SceneAnalyzer(
                self.llm,
                budget=self.token_budget,
                max_output_tokens=self.settings.max_output_tokens,
                max_requests=self.settings.max_requests,
                id_key="id",
            )
            if self.llm is not None and self.settings.llm_policy != "off"
            else None
        )
        nlp = NLPAnalyzer(
            text_processor=self.processor,
            classifier=self.classifier,
            speaker_extractor=self.speaker_extractor,
            scene_analyzer=scene,
            analysis_settings=self.settings,
        )
        documents = []
        for ch_meta, document in all_chapter_texts:
            ch_meta.sentence_start = global_idx
            analyzed = await nlp.analyze_for_plan(
                document,
                known_characters=char_db.known_names,
                character_db=char_db,
            )
            documents.append(analyzed.document.to_dict())
            for seg in analyzed:
                if seg.type == SegmentType.SCENE_BREAK:
                    continue
                content = seg.metadata.get("text_content")
                manifest.sentences.append(
                    SentenceEntry(
                        index=global_idx,
                        text=seg.text,
                        chapter_index=ch_meta.index,
                        segment_type=seg.type.value,
                        speaker=seg.speaker,
                        scene=seg.scene,
                        emotion=seg.emotion,
                        intensity=seg.intensity,
                        text_content=content.to_dict() if content is not None else None,
                        source_ref={
                            "url": ch_meta.url,
                            "chapter": ch_meta.index,
                            **seg.metadata.get("source_ref", {}),
                        },
                        speaker_origin=seg.metadata.get("speaker_origin", "default"),
                        unresolved_reason=seg.metadata.get("unresolved_reason"),
                    )
                )
                global_idx += 1
            ch_meta.sentence_end = global_idx
            manifest.chapters.append(ch_meta)
        manifest.source_documents = documents
        semantic = {
            "documents": documents,
            "analysis": self.settings.model_dump(),
            "strategy": self.analysis_strategy,
            "rules_version": "speech-quality-2",
            "analysis_backend": self.llm.analysis_identity()
            if callable(getattr(self.llm, "analysis_identity", None))
            else {},
            "sentences": [vars(s) for s in manifest.sentences],
        }
        manifest.source_fingerprint = fingerprint(documents, length=None)
        manifest.config_fingerprint = fingerprint(semantic, length=None)
        manifest.plan_id = "plan_" + fingerprint(semantic)

        if char_db.characters:
            await self._generate_voice_params(char_db, mode)

        # キャラクター情報をマニフェストに保存
        from dataclasses import asdict

        manifest.characters = {name: asdict(char) for name, char in char_db.characters.items()}
        manifest.analysis_complete = True

        # マニフェスト保存
        from ..runtime_paths import output_dir as default_output_dir

        base_dir = Path(output_dir or default_output_dir()).expanduser().resolve()
        manifest.save(base_dir)
        logger.info(
            f"Analysis complete: {len(manifest.sentences)} sentences, "
            f"{len(manifest.characters)} characters"
        )
        return manifest

    def _split_windows(self, text: str, window_size: int) -> list[str]:
        """テキストをウィンドウサイズで分割."""
        if len(text) <= window_size:
            return [text]

        windows = []
        pos = 0
        while pos < len(text):
            end = min(pos + window_size, len(text))
            # 文末で切る
            if end < len(text):
                for marker in ("。", "！", "？", "\n"):
                    last = text.rfind(marker, pos, end)
                    if last > pos:
                        end = last + 1
                        break
            windows.append(text[pos:end])
            pos = end
        return windows

    async def _discover_characters(self, text: str, char_db: CharacterDB) -> None:
        """テキストウィンドウからキャラクターを発見."""
        known = []
        for name, char in char_db.characters.items():
            known.append(f"{name} ({char.gender or '?'}, {char.age_group or '?'})")
        known_str = "\n".join(known) if known else "なし"

        prompt = CHAR_DISCOVERY_PROMPT.format(known_chars=known_str, text=text)
        try:
            result = await self.llm.generate_json(
                prompt, system=CHAR_DISCOVERY_SYSTEM, task="character_analysis"
            )
        except Exception as e:
            logger.warning(f"Character discovery failed: {e}")
            return

        if isinstance(result, list):
            for char_data in result:
                if not isinstance(char_data, dict) or "name" not in char_data:
                    continue
                char_db.get_or_create(
                    char_data["name"],
                    profile_hint={
                        "gender": char_data.get("gender"),
                        "age_group": char_data.get("age_group"),
                        "personality": char_data.get("personality"),
                    },
                )
        elif isinstance(result, dict) and "merge" in result:
            # エイリアスマージ（将来の拡張用）
            pass

    async def _generate_voice_params(self, char_db: CharacterDB, mode: str = "voisona") -> None:
        """性別・年代からアーキタイプを決め、VoiceProfileの設定を割り当てる。"""
        from ..tools.voice_profile import ARCHETYPE_NAMES

        deterministic: list[tuple[str, Any, str]] = []
        for name, char in char_db.characters.items():
            archetype = self._deterministic_archetype(char.gender, char.age_group)
            if archetype:
                deterministic.append((name, char, archetype))

        result: list[dict[str, str]] = [
            {"name": name, "archetype": archetype} for name, _, archetype in deterministic
        ]
        # 性別・年代が不明な人物は保留する。数値やアーキタイプを別LLM要求で推測しない。

        # Track archetype usage for noise differentiation
        archetype_count: dict[str, int] = {}

        for entry in result:
            if not isinstance(entry, dict) or "name" not in entry:
                continue
            name = entry["name"]
            char = char_db.characters.get(name)
            if not char:
                continue

            archetype = entry.get("archetype", "female_young")
            if archetype not in ARCHETYPE_NAMES:
                archetype = "female_young"

            if mode == "voicevox":
                self._assign_voicevox_params(char, archetype, name, archetype_count)
            elif mode == "voicepeak":
                self._assign_voicepeak_params(char, archetype, name, archetype_count)
            else:
                self._assign_voisona_params(char, archetype, name, archetype_count)

        char_db._save()
        logger.info(f"Voice archetypes assigned for {len(result)} characters")

    @staticmethod
    def _deterministic_archetype(gender: str | None, age_group: str | None) -> str | None:
        """性別・年代だけで一意に決まる場合はLLMを使わない。"""

        if gender not in {"male", "female"}:
            return None
        age_map = {
            "child": "child",
            "teen": "young",
            "young_adult": "young",
            "adult": "adult",
            "elder": "elder",
        }
        suffix = age_map.get(age_group or "")
        return f"{gender}_{suffix}" if suffix else None

    def _assign_voisona_params(
        self,
        char: Any,
        archetype: str,
        name: str,
        archetype_count: dict[str, int],
    ) -> None:
        """VoiSona用パラメータ割当."""
        if self.voice_profile and archetype in self.voice_profile.presets:
            archetype_count[archetype] = archetype_count.get(archetype, 0) + 1
            noise_seed = name if archetype_count[archetype] > 1 else None
            params = self.voice_profile.compute_params(
                preset=archetype,
                emotion="neutral",
                intensity=0.0,
                noise_seed=noise_seed,
            )
            params.pop("style_weights", None)
            char.base_params = params
            char.base_params["_archetype"] = archetype
        else:
            from ..tools.voice_profile import VoiceProfile

            fallback = VoiceProfile.create_default(
                self.voice_profile.voice_name if self.voice_profile else "",
                "",
            )
            suggested = fallback.suggest_preset_params(archetype)
            char.base_params = suggested
            char.base_params["_archetype"] = archetype

    def _assign_voicevox_params(
        self,
        char: Any,
        archetype: str,
        name: str,
        archetype_count: dict[str, int],
    ) -> None:
        """VOICEVOX用パラメータ割当."""
        if self.voice_profile and archetype in self.voice_profile.presets:
            archetype_count[archetype] = archetype_count.get(archetype, 0) + 1
            noise_seed = name if archetype_count[archetype] > 1 else None
            params = self.voice_profile.compute_params(
                preset=archetype,
                emotion="neutral",
                intensity=0.0,
                noise_seed=noise_seed,
            )
            char.base_params = params
            char.base_params["_archetype"] = archetype
        else:
            from ..tools.voicevox_profile import VoicevoxVoiceProfile

            fallback = VoicevoxVoiceProfile.create_default()
            suggested = fallback.suggest_preset_params(archetype)
            char.base_params = suggested
            char.base_params["_archetype"] = archetype

    def _assign_voicepeak_params(
        self,
        char: Any,
        archetype: str,
        name: str,
        archetype_count: dict[str, int],
    ) -> None:
        """VOICEPEAK用パラメータ割当."""
        if self.voice_profile and archetype in self.voice_profile.presets:
            archetype_count[archetype] = archetype_count.get(archetype, 0) + 1
            noise_seed = name if archetype_count[archetype] > 1 else None
            params = self.voice_profile.compute_params(
                preset=archetype,
                emotion="neutral",
                intensity=0.0,
                noise_seed=noise_seed,
            )
            char.base_params = params
            char.base_params["_archetype"] = archetype
        else:
            from ..tools.voicepeak_profile import VoicepeakVoiceProfile

            fallback = VoicepeakVoiceProfile.create_default()
            suggested = fallback.suggest_preset_params(archetype)
            char.base_params = suggested
            char.base_params["_archetype"] = archetype

    async def _analyze_detail_resilient(
        self,
        sentences: list[str],
        seg_info: list[dict],
        char_db: CharacterDB,
        context: str,
        *,
        retry_missing: bool = True,
    ) -> dict[int, dict]:
        """旧Batch呼び出し元用adapter。窓と応答検証はSceneAnalyzerに集約する。"""
        if self.llm is None or self.settings.llm_policy == "off":
            return {}
        scene = SceneAnalyzer(
            self.llm,
            budget=self.token_budget,
            max_output_tokens=self.settings.max_output_tokens,
            max_requests=self.settings.max_requests,
            id_key="id",
        )
        segments = [
            TextSegment(
                text,
                SegmentType(seg_info[i].get("type", "narration")),
                i,
                metadata={
                    "speaker": seg_info[i].get("speaker"),
                    "speaker_origin": "rule" if seg_info[i].get("speaker") else "default",
                },
            )
            for i, text in enumerate(sentences)
        ]
        results = await scene.analyze_batch(segments, char_db.known_names, context=context)
        return {
            r.index: {
                "speaker": r.speaker,
                "scene": r.scene,
                "emotion": r.emotion,
                "intensity": r.intensity,
                "new_character": r.new_character,
            }
            for r in results
        }

    _analyze_detail = _analyze_detail_resilient
