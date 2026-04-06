"""EXボイスクリップカタログ — WAVファイルの読み込みと検索."""

from __future__ import annotations

import io
import re
import wave
from dataclasses import dataclass
from pathlib import Path

from ..nlp.splitter import Chunk

# ファイル名パターン: NNN_テキスト.wav
_FILENAME_RE = re.compile(r"^(\d+)_(.+)\.wav$")

# キーワード → タグ（scene/emotion の語彙に合わせる）
# scene: daily, battle, romance, tense, comedy, sad, horror
# emotion: happy, angry, sad, surprised, scared, gentle
_KEYWORD_TAGS: list[tuple[str, frozenset[str]]] = [
    # battle
    ("いっくぞ", frozenset({"battle", "happy"})),
    ("突撃", frozenset({"battle"})),
    ("覚悟しろ", frozenset({"battle", "tense"})),
    ("奥の手", frozenset({"battle", "tense"})),
    ("奥義", frozenset({"battle", "happy"})),
    ("チャージ", frozenset({"battle"})),
    ("デストロイ", frozenset({"battle"})),
    ("シュバ", frozenset({"battle", "comedy"})),
    ("衛生兵", frozenset({"battle", "scared"})),
    ("退避", frozenset({"battle", "scared"})),
    ("逃げろ", frozenset({"battle", "scared"})),
    ("待ってろ", frozenset({"battle"})),
    ("サーチ", frozenset({"battle"})),
    ("なめんな", frozenset({"battle", "angry"})),
    ("かかってきな", frozenset({"battle", "angry"})),
    ("意識をしっかり", frozenset({"battle", "tense"})),
    ("次も決め", frozenset({"battle", "happy"})),
    ("当たれ", frozenset({"battle"})),
    ("まっかせなさ", frozenset({"battle", "happy"})),
    ("私が行く", frozenset({"battle"})),
    ("変身", frozenset({"battle", "happy"})),
    ("ビーム", frozenset({"battle", "happy"})),
    ("エネルギー注入", frozenset({"battle"})),
    ("ナイスシュー", frozenset({"battle", "happy"})),
    ("チャレンジ", frozenset({"battle", "happy"})),
    # happy
    ("よっしゃ", frozenset({"happy"})),
    ("うれし", frozenset({"happy"})),
    ("すごい", frozenset({"happy", "surprised"})),
    ("受かった", frozenset({"happy"})),
    ("やった", frozenset({"happy"})),
    ("のった", frozenset({"happy"})),
    ("最高", frozenset({"happy"})),
    ("よかった", frozenset({"happy"})),
    ("すっごく", frozenset({"happy"})),
    ("たーまや", frozenset({"happy", "comedy"})),
    ("えっへん", frozenset({"happy", "comedy"})),
    ("わっしょい", frozenset({"happy", "comedy"})),
    # comedy
    ("ちょっとー", frozenset({"comedy", "surprised"})),
    ("ちょーっと", frozenset({"comedy", "angry"})),
    ("ダッサ", frozenset({"comedy"})),
    ("変なやつ", frozenset({"comedy"})),
    ("顔よりデカ", frozenset({"comedy"})),
    ("ぬわーっ", frozenset({"comedy", "surprised"})),
    ("あっつーい", frozenset({"comedy", "daily"})),
    ("くだらない", frozenset({"comedy"})),
    ("はいはーい", frozenset({"comedy", "daily"})),
    ("オーマイガー", frozenset({"comedy", "surprised"})),
    ("やんのか", frozenset({"comedy", "angry"})),
    ("上からくるぞ", frozenset({"comedy", "battle"})),
    ("すり替えておいた", frozenset({"comedy"})),
    ("天井はいや", frozenset({"comedy"})),
    ("ウナは食べ物じゃない", frozenset({"comedy"})),
    ("日焼けした", frozenset({"comedy"})),
    ("ほっぺたむに", frozenset({"comedy", "daily"})),
    ("エイム", frozenset({"comedy", "battle"})),
    # sad / apology
    ("ごめんなさい", frozenset({"sad"})),
    ("最悪", frozenset({"sad", "angry"})),
    ("ショック", frozenset({"sad", "surprised"})),
    ("自信ない", frozenset({"sad"})),
    ("クリアできない", frozenset({"sad"})),
    ("知らん", frozenset({"sad", "angry"})),
    ("やめてや", frozenset({"sad"})),
    ("本気で言ってる", frozenset({"sad", "surprised"})),
    ("どひどい", frozenset({"sad", "angry"})),
    ("もうー", frozenset({"sad", "comedy"})),
    # scared / tense
    ("助けて", frozenset({"scared", "tense"})),
    ("痛った", frozenset({"scared", "battle"})),
    ("くぅー", frozenset({"tense", "battle"})),
    # daily / gentle
    ("こんにちは", frozenset({"daily"})),
    ("いただきます", frozenset({"daily", "happy"})),
    ("いただっきまーす", frozenset({"daily", "happy"})),
    ("おなかペコペコ", frozenset({"daily", "sad"})),
    ("うまっ", frozenset({"daily", "happy"})),
    ("焼肉", frozenset({"daily", "happy"})),
    ("アイドル", frozenset({"daily"})),
    ("ウナの日", frozenset({"daily"})),
    ("音街ウナ", frozenset({"daily"})),
    ("もうカッコよすぎ", frozenset({"happy", "romance"})),
    ("って私かー", frozenset({"surprised", "comedy"})),
    ("全然自信ない", frozenset({"sad"})),
]


@dataclass(frozen=True)
class VoiceClip:
    clip_id: str       # "041"
    text: str          # "ごめんなさい"
    path: Path
    tags: frozenset[str]


def _infer_tags(text: str) -> frozenset[str]:
    """テキストからキーワードマッチングでタグを導出."""
    result: set[str] = set()
    for keyword, tags in _KEYWORD_TAGS:
        if keyword in text:
            result.update(tags)
    return frozenset(result) if result else frozenset({"daily"})


def load_catalog(wav_dir: Path) -> list[VoiceClip]:
    """VoiceWavディレクトリからクリップカタログを読み込む."""
    clips: list[VoiceClip] = []
    for path in sorted(wav_dir.glob("*.wav")):
        m = _FILENAME_RE.match(path.name)
        if not m:
            continue
        clip_id, text = m.group(1), m.group(2)
        clips.append(VoiceClip(
            clip_id=clip_id,
            text=text,
            path=path,
            tags=_infer_tags(text),
        ))
    return clips


def find_text_matches(
    chunks: list[Chunk],
    catalog: list[VoiceClip],
) -> dict[int, VoiceClip]:
    """チャンクテキストに部分一致するクリップを返す.

    Returns:
        {chunk.index: VoiceClip} — 最初にヒットしたクリップ（1チャンク1クリップ）
    """
    result: dict[int, VoiceClip] = {}
    for chunk in chunks:
        if not chunk.text.strip():
            continue
        for clip in catalog:
            if clip.text in chunk.text or chunk.text in clip.text:
                result[chunk.index] = clip
                break  # 1チャンクにつき1クリップ
    return result


def normalize_wav(wav_bytes: bytes, target_sampwidth: int = 2) -> bytes:
    """WAVバイト列をターゲットのビット幅に正規化して返す.

    EXボイスクリップ（48kHz/24bit）をTTS出力（16bit）に合わせるために使う。

    Args:
        wav_bytes: 入力WAVバイト列
        target_sampwidth: 目標サンプル幅（バイト）。2=16bit, 3=24bit
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        src_ch = w.getnchannels()
        src_width = w.getsampwidth()
        src_rate = w.getframerate()
        pcm = w.readframes(w.getnframes())

    if src_width == target_sampwidth:
        return wav_bytes  # 変換不要

    converted = _convert_pcm_width(pcm, src_width, target_sampwidth)

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(src_ch)
        w.setsampwidth(target_sampwidth)
        w.setframerate(src_rate)
        w.writeframes(converted)
    return out.getvalue()


def _convert_pcm_width(pcm: bytes, src: int, dst: int) -> bytes:
    """PCM生データのサンプル幅をnumpyで変換する.

    24bit LE → 16bit LE: 上位16bitを取る（右シフト8bit相当）
    16bit LE → 24bit LE: 下位バイト0埋めで拡張
    """
    import numpy as np

    if src == 3 and dst == 2:
        # 24bit LE: 3バイト/サンプルを int32 に拡張してから右シフト
        arr = np.frombuffer(pcm, dtype=np.uint8).reshape(-1, 3)
        # 符号付き32bitに拡張（上位バイトで符号延長）
        s32 = (
            arr[:, 0].astype(np.int32)
            | (arr[:, 1].astype(np.int32) << 8)
            | (arr[:, 2].astype(np.int32) << 16)
        )
        # 符号延長（24bit符号付き → 32bit符号付き）
        s32 = np.where(s32 >= 0x800000, s32 - 0x1000000, s32)
        s16 = np.clip(s32 >> 8, -32768, 32767).astype(np.int16)
        return s16.tobytes()
    elif src == 2 and dst == 3:
        arr = np.frombuffer(pcm, dtype=np.int16).astype(np.int32) << 8
        b = arr.view(np.uint8).reshape(-1, 4)
        return b[:, :3].tobytes()
    elif src == 4 and dst == 2:
        arr = np.frombuffer(pcm, dtype=np.int32)
        return (arr >> 16).astype(np.int16).tobytes()
    elif src == 2 and dst == 4:
        arr = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
        return (arr << 16).tobytes()
    else:
        raise ValueError(f"Unsupported PCM width conversion: {src} → {dst}")
