"""EXボイスクリップ選択 — LLMによる自然な挿入判断."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from ..nlp.llm_backend import LLMBackend
from ..nlp.scene_analyzer import AnalyzedSegment
from ..nlp.splitter import Chunk
from .catalog import VoiceClip

_SELECTOR_SYSTEM = """\
あなたは小説の朗読に挿入するキャラクターボイスクリップの選定専門家です。
テキストの流れを自然に演出するため、感情的に適切な箇所にのみクリップを挿入してください。
挿入は控えめに行い、不自然・過剰にならないようにしてください。
"""

_SELECTOR_PROMPT = """\
## 朗読チャンク一覧（チャンクID・テキスト・シーン・感情）
{chunks_text}

## 挿入候補クリップ（clip_id: テキスト）
{clips_text}

## 指示
上のチャンクを朗読する際、各チャンクの**後ろ**に挿入すると自然なEXボイスクリップを最大{max_insertions}個選んでください。
- 感情やシーンの転換点、緊張・爆笑・感動のピークなど、効果的な箇所のみ選ぶこと
- 全く適切なクリップがなければ空配列を返すこと
- 1チャンクにつき1クリップまで

JSON配列のみを出力（説明不要）:
[{{"after_chunk_index": <int>, "clip_id": "<string>"}}]
"""


@dataclass
class ClipInsertion:
    after_chunk_index: int
    clip: VoiceClip
    source: str  # "llm" | "text_match"


_MIN_CHUNK_CHARS = 10  # これ未満のチャンクはLLM判断から除外


class ExVoiceSelector:
    """LLMを用いてチャンク列に対するクリップ挿入を決定."""

    def __init__(
        self,
        backend: LLMBackend,
        catalog: list[VoiceClip],
        prefilter_top_n: int = 40,
    ):
        self._backend = backend
        self._catalog = catalog
        self._prefilter_top_n = prefilter_top_n

    async def select(
        self,
        chunks: list[Chunk],
        analyzed_cache: dict[int, list[AnalyzedSegment]],
        max_insertions: int = 2,
    ) -> list[ClipInsertion]:
        """チャンク窓に対してクリップ挿入を決定."""
        # 短すぎるチャンクはLLM判断対象外
        chunks = [c for c in chunks if len(c.text.strip()) >= _MIN_CHUNK_CHARS]
        if not chunks:
            return []

        candidates = self._prefilter(chunks, analyzed_cache)
        if not candidates:
            logger.debug("ExVoice: no candidate clips after prefilter")
            return []

        chunks_text = self._format_chunks(chunks, analyzed_cache)
        clips_text = "\n".join(
            f"{c.clip_id}: {c.text}" for c in candidates
        )
        prompt = _SELECTOR_PROMPT.format(
            chunks_text=chunks_text,
            clips_text=clips_text,
            max_insertions=max_insertions,
        )

        try:
            raw = await self._backend.generate_json(
                prompt, system=_SELECTOR_SYSTEM, temperature=0.2
            )
        except Exception as e:
            logger.warning(f"ExVoice LLM selection failed: {e}")
            return []

        valid_indices = {c.index for c in chunks}
        candidate_map = {c.clip_id: c for c in candidates}
        return _parse_response(raw, candidate_map, valid_indices)

    def _prefilter(
        self,
        chunks: list[Chunk],
        analyzed_cache: dict[int, list[AnalyzedSegment]],
    ) -> list[VoiceClip]:
        """NLP分析のscene/emotionタグでカタログを事前フィルタ."""
        window_tags: set[str] = set()
        for idx in (c.index for c in chunks):
            for seg in analyzed_cache.get(idx, []):
                window_tags.add(seg.scene)
                window_tags.add(seg.emotion)

        # タグ重複スコアで上位N件を選ぶ
        scored: list[tuple[int, VoiceClip]] = []
        for clip in self._catalog:
            overlap = len(clip.tags & window_tags)
            if overlap > 0:
                scored.append((overlap, clip))

        scored.sort(key=lambda x: -x[0])
        result = [clip for _, clip in scored[: self._prefilter_top_n]]

        # タグ一致がなければ全クリップを対象にする（フォールバック）
        if not result:
            result = self._catalog[: self._prefilter_top_n]

        return result

    @staticmethod
    def _format_chunks(
        chunks: list[Chunk],
        analyzed_cache: dict[int, list[AnalyzedSegment]],
    ) -> str:
        lines = []
        for chunk in chunks:
            segs = analyzed_cache.get(chunk.index, [])
            if segs:
                dominant = max(segs, key=lambda s: len(s.text))
                scene = dominant.scene
                emotion = dominant.emotion
            else:
                scene = emotion = "unknown"
            lines.append(f"[{chunk.index}] {chunk.text[:60]}  scene={scene} emotion={emotion}")
        return "\n".join(lines)


def _parse_response(
    raw: list | dict,
    candidate_map: dict[str, VoiceClip],
    valid_indices: set[int],
) -> list[ClipInsertion]:
    if not isinstance(raw, list):
        return []
    results: list[ClipInsertion] = []
    seen_indices: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        idx = item.get("after_chunk_index")
        cid = str(item.get("clip_id", "")).strip()
        if not isinstance(idx, int) or idx not in valid_indices:
            continue
        if cid not in candidate_map:
            continue
        if idx in seen_indices:
            continue  # 1チャンクにつき1クリップ
        seen_indices.add(idx)
        results.append(ClipInsertion(
            after_chunk_index=idx,
            clip=candidate_map[cid],
            source="llm",
        ))
    return results
