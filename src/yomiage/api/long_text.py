"""長文 TTS 合成ユーティリティ."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..nlp.splitter import TextSplitter
from ..tts.audio_utils import concat_wav_bytes, write_silence_wav
from .models import SynthesisResult

if TYPE_CHECKING:
    from .bridge import TTSBridge


@dataclass
class LongTextOptions:
    """長文合成オプション."""

    max_chars: int = 200
    pause_between_chunks: float = 0.0
    sample_rate: int = 24000


async def synthesize_long_text(
    bridge: "TTSBridge",
    text: str,
    *,
    options: LongTextOptions | None = None,
    **synth_kwargs: object,
) -> SynthesisResult:
    """長文を自動分割して合成し、1つの音声に結合.

    Args:
        bridge: TTS ブリッジ
        text: 合成する長文
        options: 分割・結合オプション
        **synth_kwargs: bridge.synthesize() に渡すパラメータ

    Returns:
        結合された SynthesisResult
    """
    opts = options or LongTextOptions()
    splitter = TextSplitter(max_chars=opts.max_chars)
    chunks = splitter.split(text)

    if not chunks:
        return SynthesisResult(audio_data=b"", format="wav")

    wav_parts: list[bytes] = []
    total_duration = 0.0
    last_sample_rate: int | None = None

    for chunk in chunks:
        if chunk.is_scene_break or not chunk.text.strip():
            continue

        result = await bridge.synthesize(chunk.text, **synth_kwargs)
        if result.audio_data:
            wav_parts.append(result.audio_data)
            total_duration += result.duration or 0.0
            if result.sample_rate:
                last_sample_rate = result.sample_rate

        if opts.pause_between_chunks > 0:
            silence_path = Path("__tmp_silence__.wav")
            try:
                write_silence_wav(
                    silence_path,
                    opts.pause_between_chunks,
                    sample_rate=opts.sample_rate,
                )
                wav_parts.append(silence_path.read_bytes())
                total_duration += opts.pause_between_chunks
            finally:
                silence_path.unlink(missing_ok=True)

    if not wav_parts:
        return SynthesisResult(audio_data=b"", format="wav")

    merged = concat_wav_bytes(wav_parts)
    return SynthesisResult(
        audio_data=merged,
        format="wav",
        sample_rate=last_sample_rate or opts.sample_rate,
        duration=total_duration,
    )
