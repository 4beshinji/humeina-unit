"""Phase A: 2-stage LLM full-text analysis for batch pipeline."""

import hashlib

from loguru import logger

from ..nlp.classifier import SegmentType, TextClassifier
from ..nlp.ollama_client import OllamaClient
from ..nlp.speaker import SpeakerExtractor
from ..nlp.splitter import TextSplitter
from ..nlp.text_processor import TextProcessor
from ..reader.character_db import CharacterDB
from ..sources import registry
from ..sources.base import ChapterInfo
from .manifest import BatchManifest, ChapterMeta, SentenceEntry

# --- Pass 1 prompts ---

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
音声合成パラメータを設計してください。JSON形式で回答してください。"""

CHAR_VOICE_PROMPT = """\
キャラクター一覧:
{characters}

各キャラクターに以下のパラメータを設定してください（VoiSona Talk用）:
- pitch: -600〜600（低い声:-200〜-100, 高い声:100〜200）
- huskiness: -20〜20（ハスキー:10〜15, クリア:-5〜-10）
- alp: -1.0〜1.0（大人っぽい:-0.3, 子供っぽい:0.3）
- speed: 0.5〜2.0（ゆっくり:0.8〜0.9, 速い:1.1〜1.2）

JSON配列で回答:
[{{"name": "キャラ名", "pitch": 0, "huskiness": 0, "alp": 0.0, "speed": 1.0}}]"""

# --- Pass 2 prompts ---

DETAIL_ANALYSIS_SYSTEM = """\
あなたは小説テキスト分析器です。各文を詳細に分析してください。JSON形式で回答してください。"""

DETAIL_ANALYSIS_PROMPT = """\
キャラクター一覧: {characters}
前のシーンの状況: {context}

以下の文を分析してください:
{sentences}

各文についてJSON配列で回答:
[{{"id": 0, "speaker": "キャラ名" or null, \
"scene": "daily/battle/romance/tense/comedy/sad/horror", \
"emotion": "neutral/happy/angry/sad/surprised/scared/gentle", \
"intensity": 0.0-1.0, \
"viewpoint": "視点キャラ名" or null}}]"""


class BatchAnalyzer:
    """2段階LLM全文分析."""

    def __init__(
        self,
        ollama: OllamaClient,
        text_processor: TextProcessor | None = None,
        classifier: TextClassifier | None = None,
        speaker_extractor: SpeakerExtractor | None = None,
        analysis_window_chars: int = 3000,
        analysis_window_sentences: int = 25,
    ):
        self.ollama = ollama
        self.processor = text_processor or TextProcessor()
        self.classifier = classifier or TextClassifier()
        self.speaker_extractor = speaker_extractor or SpeakerExtractor()
        self.window_chars = analysis_window_chars
        self.window_sentences = analysis_window_sentences

    async def analyze(
        self,
        url: str,
        mode: str = "voisona",
        output_dir: str = "output",
        chapter_range: tuple[int, int] | None = None,
    ) -> BatchManifest:
        """URL → 全文分析 → BatchManifest を生成."""
        from pathlib import Path

        source = registry.resolve(url)
        work_id = hashlib.md5(url.encode()).hexdigest()[:12]

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
        )

        char_db = CharacterDB(work_id)

        # --- Pass 1: キャラクター発見 ---
        logger.info("=== Pass 1: Character discovery ===")
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

            # ウィンドウ分割してキャラ発見
            windows = self._split_windows(clean_text, self.window_chars)
            for window in windows:
                await self._discover_characters(window, char_db)

        logger.info(f"Discovered {len(char_db.characters)} characters")

        # キャラクターボイスパラメータ生成（VoiSonaモード）
        if mode == "voisona" and char_db.characters:
            await self._generate_voice_params(char_db)

        # --- Pass 2: 章ごと詳細分析 ---
        logger.info("=== Pass 2: Detailed analysis ===")
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

            # ウィンドウ単位でLLM詳細分析
            for win_start in range(0, len(sentences), self.window_sentences):
                win_end = min(win_start + self.window_sentences, len(sentences))
                win_sentences = sentences[win_start:win_end]
                win_seg_info = [seg_map.get(win_start + j, {}) for j in range(len(win_sentences))]

                analysis = await self._analyze_detail(
                    win_sentences, win_seg_info, char_db, context
                )

                for j, sent_text in enumerate(win_sentences):
                    seg_info = win_seg_info[j]
                    llm_info = analysis.get(j, {})

                    # セグメントタイプ判定
                    seg_type = seg_info.get("type", "narration")

                    speaker = llm_info.get("speaker") or seg_info.get("speaker")
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

            ch_meta.sentence_end = global_idx
            manifest.chapters.append(ch_meta)
            logger.info(
                f"Chapter {ch_meta.index + 1} analyzed: "
                f"{ch_meta.sentence_end - ch_meta.sentence_start} sentences"
            )

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
            result = await self.ollama.generate_json(prompt, system=CHAR_DISCOVERY_SYSTEM)
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

    async def _generate_voice_params(self, char_db: CharacterDB) -> None:
        """LLMでキャラクターごとのVoiSonaパラメータを生成."""
        chars_desc = []
        for name, char in char_db.characters.items():
            chars_desc.append(
                f"- {name}: {char.gender or '不明'}, {char.age_group or '不明'}, "
                f"{char.personality or '不明'}"
            )

        prompt = CHAR_VOICE_PROMPT.format(characters="\n".join(chars_desc))
        try:
            result = await self.ollama.generate_json(prompt, system=CHAR_VOICE_SYSTEM)
        except Exception as e:
            logger.warning(f"Voice param generation failed: {e}")
            return

        if not isinstance(result, list):
            return

        for entry in result:
            if not isinstance(entry, dict) or "name" not in entry:
                continue
            name = entry["name"]
            char = char_db.characters.get(name)
            if not char:
                continue
            char.base_params = {
                k: entry[k]
                for k in ("pitch", "huskiness", "alp", "speed")
                if k in entry
            }

        char_db._save()
        logger.info(f"Voice params generated for {len(result)} characters")

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
            result = await self.ollama.generate_json(
                prompt, system=DETAIL_ANALYSIS_SYSTEM
            )
        except Exception as e:
            logger.warning(f"Detail analysis failed: {e}")
            return {}

        analysis: dict[int, dict] = {}
        if isinstance(result, list):
            for r in result:
                if isinstance(r, dict) and "id" in r:
                    analysis[r["id"]] = r

        return analysis
