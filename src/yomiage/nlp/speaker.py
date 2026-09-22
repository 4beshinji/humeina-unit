"""Speaker identification — rule-based + SLM."""

import re

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
        self._before_pattern = re.compile(rf"([\w\u3000-\u9fff]+?){self._SUBJECT_MARKERS}")
        # パターン2: 「...」と〇〇が言った / 「...」〇〇は言った
        self._after_pattern = re.compile(
            rf"(?:と|って)?([\w\u3000-\u9fff]+?){self._SUBJECT_MARKERS}\s*(?:{verbs_pattern})"
        )
        # パターン3: 〇〇「...」（名前の直後に括弧）
        self._direct_pattern = re.compile(r"([\w\u3000-\u9fff]{2,})「")

    def extract(
        self,
        segments: list[TextSegment],
        known_characters: list[str] | None = None,
    ) -> list[TextSegment]:
        """候補と確定話者を区別する。直前の主語だけでは確定しない。"""
        verbs = "|".join(self._SPEECH_VERBS)
        known = set(known_characters or ())
        name_pattern = r"([^。！？!?、\s「」]+?)[はがも]"
        for i, seg in enumerate(segments):
            if seg.type != SegmentType.DIALOGUE:
                seg.metadata.setdefault("speaker_origin", "default")
                continue
            if seg.metadata.get("speaker_origin") == "explicit":
                continue
            prev = (
                segments[i - 1].text if i and segments[i - 1].type == SegmentType.NARRATION else ""
            )
            following = (
                segments[i + 1].text
                if i + 1 < len(segments) and segments[i + 1].type == SegmentType.NARRATION
                else ""
            )
            candidates: list[str] = []
            strong: list[str] = []
            matches = list(re.finditer(name_pattern, prev))
            if matches:
                match = matches[-1]
                name = self._clean_candidate(match.group(1))
                candidates.append(name)
                tail = prev[match.end() :].strip()
                if re.fullmatch(rf"(?:{verbs})[^。！？!?]*[。！？!?]?", tail) or (
                    not tail and (name in known or re.match(rf"^と(?:{verbs})", following))
                ):
                    strong.append(name)
            direct = re.fullmatch(r"([^。！？!?、\s「」]+)", prev)
            if direct and direct.group(1) in known:
                strong.append(direct.group(1))
            after = re.match(rf"^(?:と|って){name_pattern}\s*(?:{verbs})", following)
            if after:
                strong.append(self._clean_candidate(after.group(1)))
            seg.speaker_candidates = list(dict.fromkeys(candidates + strong))
            strong = list(dict.fromkeys(strong))
            if len(strong) == 1:
                seg.metadata.update(
                    speaker=strong[0], speaker_origin="rule", unresolved_reason=None
                )
            else:
                seg.metadata.update(
                    speaker=None,
                    speaker_origin="default",
                    unresolved_reason="conflicting_speakers" if strong else "no_speaker_evidence",
                )
        return segments

    @staticmethod
    def _clean_candidate(value: str) -> str:
        """文脈込みでマッチした候補から直近の名前部分を取り出す."""

        # ``\u3000-\u9fff`` の範囲には句読点も含まれるため、正規表現のマッチが
        # ``と言った。花子`` のように複数文を含む場合がある。
        pieces = re.split(r"[。．、，！？!?\n\r\t ]+", value.strip())
        return next(
            (piece.strip("「」『』()（）") for piece in reversed(pieces) if piece.strip()),
            "",
        )
