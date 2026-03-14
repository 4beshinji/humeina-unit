"""Speaker identification — rule-based + SLM."""

import re

from loguru import logger

from .classifier import SegmentType, TextSegment


class SpeakerExtractor:
    """ルールベースの話者候補抽出.

    「〇〇は言った」「〇〇が叫んだ」等のパターンから話者候補を付与.
    """

    # 発話動詞パターン
    _SPEECH_VERBS = (
        "言[っいう]",
        "話[しす]",
        "叫[びぶん]",
        "呟[きくい]",
        "つぶやい",
        "囁[きくい]",
        "ささやい",
        "答え",
        "返[しす]",
        "尋ね",
        "聞[きくい]",
        "告げ",
        "怒鳴[りる]",
        "叱[りる]",
        "笑[っいう]",
        "微笑[みむん]",
        "頷[きくい]",
        "続け",
        "遮[りる]",
        "呼[びぶん]",
        "問[いう]",
        "口を開[きくい]",
        "声を[あ上]げ",
    )

    # 主語マーカー
    _SUBJECT_MARKERS = r"[はがも]"

    def __init__(self):
        verbs_pattern = "|".join(self._SPEECH_VERBS)
        # 「セリフ」の前後で話者を探す
        # パターン1: 〇〇は「...」と言った
        self._before_pattern = re.compile(
            rf"([\w\u3000-\u9fff]+?){self._SUBJECT_MARKERS}"
        )
        # パターン2: 「...」と〇〇が言った / 「...」〇〇は言った
        self._after_pattern = re.compile(
            rf"(?:と|って)?([\w\u3000-\u9fff]+?){self._SUBJECT_MARKERS}\s*(?:{verbs_pattern})"
        )
        # パターン3: 〇〇「...」（名前の直後に括弧）
        self._direct_pattern = re.compile(
            r"([\w\u3000-\u9fff]{2,})「"
        )

    def extract(self, segments: list[TextSegment]) -> list[TextSegment]:
        """セグメントリストに話者候補を付与."""
        for i, seg in enumerate(segments):
            if seg.type != SegmentType.DIALOGUE:
                continue

            candidates: list[str] = []

            # 前の地の文から話者を探す
            if i > 0 and segments[i - 1].type == SegmentType.NARRATION:
                prev_text = segments[i - 1].text
                m = self._before_pattern.search(prev_text)
                if m:
                    candidates.append(m.group(1))
                m = self._direct_pattern.search(prev_text + seg.text)
                if m:
                    name = m.group(1)
                    if name not in candidates:
                        candidates.append(name)

            # 後の地の文から話者を探す
            if i + 1 < len(segments) and segments[i + 1].type == SegmentType.NARRATION:
                next_text = segments[i + 1].text
                m = self._after_pattern.search(next_text)
                if m:
                    name = m.group(1)
                    if name not in candidates:
                        candidates.append(name)

            if candidates:
                seg.speaker_candidates = candidates
                logger.debug(f"Speaker candidates for [{seg.text[:20]}...]: {candidates}")

        return segments
