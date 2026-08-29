"""Phase A: token予算ベースの原則1-pass全文分析。"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..core.identity import build_identity
from ..llm import LLMBackend, LLMError, LLMErrorCategory
from ..llm.budgeting import TokenBudget, split_by_token_budget
from ..nlp.classifier import SegmentType, TextClassifier
from ..nlp.speaker import SpeakerExtractor
from ..nlp.splitter import TextSplitter
from ..nlp.text_processor import TextProcessor
from ..ports.sources import URLPolicy
from ..reader.character_db import CharacterDB
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

CHAR_VOICE_SYSTEM = """\
あなたはTTSパラメータ設計者です。キャラクターの特徴に基づいて、\
最も近いアーキタイプを選んでください。JSON形式で回答してください。"""

CHAR_VOICE_PROMPT = """\
キャラクター一覧:
{characters}

各キャラクターに最も近いアーキタイプを選んでください:
  male_child（男児）, male_young（若い男性）, male_adult（成人男性）, male_elder（老年男性）,
  female_child（女児）, female_young（若い女性）, female_adult（成人女性）,
  female_elder（老年女性）,
  narrator（ナレーター）

JSON配列で回答:
[{{"name": "キャラ名", "archetype": "female_young"}}]"""

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
        llm: LLMBackend,
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
    ):
        self.llm = llm
        self.processor = text_processor or TextProcessor()
        self.classifier = classifier or TextClassifier()
        self.speaker_extractor = speaker_extractor or SpeakerExtractor()
        self.window_chars = analysis_window_chars
        self.window_sentences = analysis_window_sentences
        self.token_budget = TokenBudget(
            context_tokens=context_tokens,
            safety_ratio=context_safety_ratio,
            max_items=analysis_window_sentences,
        )
        self.analysis_strategy = analysis_strategy
        self.voice_profile = voice_profile
        self.url_policy = url_policy

    async def analyze(
        self,
        url: str,
        mode: str = "voisona",
        output_dir: str = "output",
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
            clean_text = self.processor.process(chapter.text)

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
            all_chapter_texts.append((ch_meta, clean_text))

            # 品質比較用の明示的exhaustiveモードだけ旧人物発見passを行う。
            if self.analysis_strategy == "exhaustive":
                for window in self._split_windows(clean_text, self.window_chars):
                    await self._discover_characters(window, char_db)

        global_idx = 0
        context = "物語の冒頭"

        splitter = TextSplitter(max_chars=500)

        for ch_meta, clean_text in all_chapter_texts:
            ch_meta.sentence_start = global_idx

            # 文単位に分割（splitterの_split_sentencesを公開メソッドとして利用）
            sentences = splitter.split_sentences(clean_text)

            # ルールベースパイプライン適用
            segments = self.classifier.classify(clean_text)
            segments = self.speaker_extractor.extract(segments)

            # セグメントを文に対応付け
            seg_map = self._map_segments_to_sentences(sentences, segments)

            # 入力・予想JSON出力・最大項目数からbatchを動的に決める。
            batches = split_by_token_budget(
                sentences,
                self.token_budget,
                output_tokens_per_item=96,
                fixed_output_tokens=512,
            )
            win_start = 0
            for win_sentences in batches:
                win_seg_info = [
                    seg_map.get(win_start + j, {}) for j in range(len(win_sentences))
                ]

                analysis = await self._analyze_detail_resilient(
                    win_sentences, win_seg_info, char_db, context
                )

                for j, sent_text in enumerate(win_sentences):
                    seg_info = win_seg_info[j]
                    llm_info = analysis.get(j, {})

                    # セグメントタイプ判定
                    seg_type = seg_info.get("type", "narration")

                    speaker = llm_info.get("speaker") or seg_info.get("speaker")
                    new_character = llm_info.get("new_character")
                    if isinstance(new_character, dict) and new_character.get("name"):
                        char_db.get_or_create(
                            str(new_character["name"]),
                            profile_hint={
                                "gender": new_character.get("gender"),
                                "age_group": new_character.get("age_group"),
                                "personality": new_character.get("personality"),
                            },
                        )
                    if speaker:
                        char_db.get_or_create(speaker)

                    entry = SentenceEntry(
                        index=global_idx,
                        text=sent_text,
                        chapter_index=ch_meta.index,
                        segment_type=seg_type,
                        speaker=speaker,
                        scene=llm_info.get("scene", "daily"),
                        emotion=llm_info.get("emotion", "neutral"),
                        intensity=llm_info.get("intensity", 0.5),
                        viewpoint_character=llm_info.get("viewpoint"),
                    )
                    manifest.sentences.append(entry)
                    global_idx += 1

                # コンテキスト更新
                if win_sentences:
                    context = "".join(win_sentences[-3:])
                win_start += len(win_sentences)

            ch_meta.sentence_end = global_idx
            manifest.chapters.append(ch_meta)
            logger.info(
                f"Chapter {ch_meta.index + 1} analyzed: "
                f"{ch_meta.sentence_end - ch_meta.sentence_start} sentences"
            )

        if char_db.characters:
            await self._generate_voice_params(char_db, mode)

        # キャラクター情報をマニフェストに保存
        from dataclasses import asdict

        manifest.characters = {
            name: asdict(char) for name, char in char_db.characters.items()
        }
        manifest.analysis_complete = True

        # マニフェスト保存
        base_dir = Path(output_dir)
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

    async def _generate_voice_params(
        self, char_db: CharacterDB, mode: str = "voisona"
    ) -> None:
        """LLMでキャラクターごとのアーキタイプを選択し、VoiceProfileからパラメータを割当."""
        from ..tools.voice_profile import ARCHETYPE_NAMES

        chars_desc = []
        deterministic: list[tuple[str, Any, str]] = []
        unresolved: list[tuple[str, Any]] = []
        for name, char in char_db.characters.items():
            archetype = self._deterministic_archetype(char.gender, char.age_group)
            if archetype:
                deterministic.append((name, char, archetype))
            else:
                unresolved.append((name, char))
                chars_desc.append(
                    f"- {name}: {char.gender or '不明'}, {char.age_group or '不明'}, "
                    f"{char.personality or '不明'}"
                )

        result: list[dict[str, str]] = [
            {"name": name, "archetype": archetype}
            for name, _, archetype in deterministic
        ]
        if unresolved:
            prompt = CHAR_VOICE_PROMPT.format(characters="\n".join(chars_desc))
            try:
                generated = await self.llm.generate_json(
                    prompt, system=CHAR_VOICE_SYSTEM, task="character_analysis"
                )
            except Exception as e:
                logger.warning(f"Voice param generation failed: {e}")
                generated = []
            if isinstance(generated, list):
                result.extend(entry for entry in generated if isinstance(entry, dict))

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
                self._assign_voicevox_params(
                    char, archetype, name, archetype_count
                )
            elif mode == "voicepeak":
                self._assign_voicepeak_params(
                    char, archetype, name, archetype_count
                )
            else:
                self._assign_voisona_params(
                    char, archetype, name, archetype_count
                )

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

    def _map_segments_to_sentences(
        self, sentences: list[str], segments: list
    ) -> dict[int, dict]:
        """文リストとセグメント分類結果を対応付け."""
        seg_map: dict[int, dict] = {}
        seg_idx = 0

        for sent_idx, sent in enumerate(sentences):
            info: dict = {"type": "narration", "speaker": None}

            # 最も近いセグメントを探す
            while seg_idx < len(segments):
                seg = segments[seg_idx]
                if seg.type == SegmentType.SCENE_BREAK:
                    seg_idx += 1
                    continue

                # テキストの一致を確認
                if seg.text and seg.text[:20] in sent or sent[:20] in seg.text:
                    info["type"] = seg.type.value
                    if seg.speaker_candidates:
                        info["speaker"] = seg.speaker_candidates[0]
                    seg_idx += 1
                    break

                seg_idx += 1
                if seg_idx > sent_idx + 5:
                    break

            seg_map[sent_idx] = info

        return seg_map

    async def _analyze_detail(
        self,
        sentences: list[str],
        seg_info: list[dict],
        char_db: CharacterDB,
        context: str,
        *,
        retry_missing: bool = True,
    ) -> dict[int, dict]:
        """文ウィンドウの詳細LLM分析."""
        chars_str = ", ".join(char_db.known_names) if char_db.known_names else "なし"

        sent_lines = []
        for i, sent in enumerate(sentences):
            seg_type = seg_info[i].get("type", "narration") if i < len(seg_info) else "narration"
            sent_lines.append(f"[{i}] ({seg_type}) {sent}")

        prompt = DETAIL_ANALYSIS_PROMPT.format(
            characters=chars_str,
            context=context[:200],
            sentences="\n".join(sent_lines),
        )

        try:
            result = await self.llm.generate_json(
                prompt,
                system=DETAIL_ANALYSIS_SYSTEM,
                task="scene_analysis",
                json_schema=DETAIL_ANALYSIS_SCHEMA,
            )
        except LLMError as e:
            if e.category == LLMErrorCategory.CONTEXT_LENGTH:
                raise
            logger.warning(f"Detail analysis failed: {e}")
            return {}
        except Exception as e:
            logger.warning(f"Detail analysis failed: {e}")
            return {}

        analysis: dict[int, dict] = {}
        if isinstance(result, list):
            for r in result:
                if isinstance(r, dict) and "id" in r:
                    analysis[r["id"]] = r

        # 正常なIDは採用済みとし、欠落した文だけを短いrequestで一度再問い合わせする。
        missing = [index for index in range(len(sentences)) if index not in analysis]
        if retry_missing and analysis and missing:
            missing_result = await self._analyze_detail_resilient(
                [sentences[index] for index in missing],
                [seg_info[index] for index in missing],
                char_db,
                context,
                retry_missing=False,
            )
            for local_index, value in missing_result.items():
                if isinstance(local_index, int) and 0 <= local_index < len(missing):
                    analysis[missing[local_index]] = value

        return analysis

    async def _analyze_detail_resilient(
        self,
        sentences: list[str],
        seg_info: list[dict],
        char_db: CharacterDB,
        context: str,
        *,
        retry_missing: bool = True,
    ) -> dict[int, dict]:
        """context超過時だけ同じProvider上でbatchを二分する。"""

        try:
            return await self._analyze_detail(
                sentences,
                seg_info,
                char_db,
                context,
                retry_missing=retry_missing,
            )
        except LLMError as error:
            if error.category != LLMErrorCategory.CONTEXT_LENGTH or len(sentences) <= 1:
                logger.warning(f"Detail analysis failed: {error}")
                return {}
        middle = len(sentences) // 2
        left = await self._analyze_detail_resilient(
            sentences[:middle],
            seg_info[:middle],
            char_db,
            context,
            retry_missing=retry_missing,
        )
        right = await self._analyze_detail_resilient(
            sentences[middle:],
            seg_info[middle:],
            char_db,
            context,
            retry_missing=retry_missing,
        )
        return {**left, **{index + middle: value for index, value in right.items()}}
