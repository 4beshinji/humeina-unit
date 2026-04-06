"""EXボイス機能フルテスト — 合成・挿入・結合・再生・保存.

VoicePeak（音街ウナ）で各チャンクを合成し、EXボイスクリップを適切な位置に挿入してから
wave モジュールで結合、output/exvoice_test.wav に保存して再生する。
"""

import asyncio
import io
import sys
import wave
from pathlib import Path

from loguru import logger

AOZORA_URL = "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"  # 羅生門
WAV_DIR = Path("/home/sin/code/una/VoiceWav")
OUTPUT_PATH = Path("output/exvoice_test.wav")
MODEL = "qwen3:8b"
# テスト用: 最初のN チャンクだけ処理（None で全文）
MAX_CHUNKS = 20


async def main():
    from yomiage.exvoice.catalog import load_catalog
    from yomiage.exvoice.manager import ExVoiceManager
    from yomiage.exvoice.selector import ExVoiceSelector
    from yomiage.nlp.classifier import TextClassifier
    from yomiage.nlp.llm_backend import OllamaBackend
    from yomiage.nlp.scene_analyzer import SceneAnalyzer
    from yomiage.nlp.speaker import SpeakerExtractor
    from yomiage.nlp.splitter import TextSplitter
    from yomiage.nlp.text_processor import TextProcessor
    from yomiage.sources.aozora import AozoraSource
    from yomiage.tts.playback import play_wav
    from yomiage.tts.voicepeak import VoicepeakProvider

    # ── テキスト取得 ─────────────────────────────────
    logger.info(f"Fetching: {AOZORA_URL}")
    chapter = await AozoraSource().fetch_chapter(AOZORA_URL)
    logger.info(f"Title: {chapter.title}")

    clean = TextProcessor().process(chapter.text)
    splitter = TextSplitter(max_chars=200)
    all_chunks = splitter.split(clean)
    speakable = [c for c in all_chunks if c.text.strip() and not c.is_scene_break]
    if MAX_CHUNKS:
        speakable = speakable[:MAX_CHUNKS]
    logger.info(f"Processing {len(speakable)} chunks")

    # ── NLP分析 ──────────────────────────────────────
    backend = OllamaBackend(model=MODEL)
    classifier = TextClassifier()
    speaker_ex = SpeakerExtractor()
    analyzer = SceneAnalyzer(backend)

    analyzed_cache: dict = {}
    logger.info("Running NLP analysis...")
    for chunk in speakable:
        segs = classifier.classify(chunk.text)
        segs = speaker_ex.extract(segs)
        analyzed = await analyzer.analyze_batch(segs)
        analyzed_cache[chunk.index] = analyzed

    # ── EXボイス選択 ──────────────────────────────────
    catalog = load_catalog(WAV_DIR)
    logger.info(f"Catalog: {len(catalog)} clips")

    ex_backend = OllamaBackend(model=MODEL)
    selector = ExVoiceSelector(ex_backend, catalog)
    manager = ExVoiceManager(
        catalog=catalog,
        selector=selector,
        cooldown_chunks=5,
        max_per_chapter=6,
        llm_max_insertions=2,
    )
    manager.reset_chapter()

    logger.info("Running ExVoice window analysis...")
    await manager.analyze_window(speakable, analyzed_cache)

    # ── 合成・挿入 ────────────────────────────────────
    vp_path = "/home/sin/code/una/Voicepeak-linux64/Voicepeak/voicepeak"
    voicepeak = VoicepeakProvider({
        "path": vp_path,
        "default_narrator": "Otomachi Una",
        "max_chars": 140,
        "pitch_scale": 300,
    })
    if not await voicepeak.is_available():
        logger.error(f"VoicePeak not available: {vp_path}")
        sys.exit(1)
    logger.info("VoicePeak (Otomachi Una) ready")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # WAVバイト列を順番に収集
    audio_parts: list[bytes] = []
    order = 0

    logger.info("Synthesizing chunks...")
    for chunk in speakable:
        segs = analyzed_cache.get(chunk.index, [])
        scene = segs[0].scene if segs else "daily"
        emotion = segs[0].emotion if segs else "neutral"

        # チャンク合成（音街ウナ）
        try:
            result = await voicepeak.synthesize(chunk.text)
            audio_parts.append(result.audio_data)
            order += 1
            logger.debug(f"  [{chunk.index:03d}] ({scene}/{emotion}) {chunk.text[:40]}…")
        except Exception as e:
            logger.warning(f"Synthesis failed for chunk {chunk.index}: {e}")
            continue

        # EXボイス挿入（チャンク合成後）
        clips = manager.pop_clips_for(chunk.index)
        for clip in clips:
            audio_parts.append(clip.path.read_bytes())
            order += 1
            logger.info(f"  ▶ EX voice → {clip.clip_id}: 「{clip.text}」  after chunk {chunk.index}")

    logger.info(f"Total parts: {order} (TTS + EX clips)")

    # ── WAV結合（wave モジュール） ────────────────────
    logger.info(f"Concatenating → {OUTPUT_PATH}")
    combined = _concat_wav_bytes(audio_parts)
    OUTPUT_PATH.write_bytes(combined)

    size_kb = OUTPUT_PATH.stat().st_size // 1024
    logger.info(f"Saved: {OUTPUT_PATH} ({size_kb} KB)")

    # ── 再生 ─────────────────────────────────────────
    logger.info("Playing...")
    await play_wav(combined)
    logger.info("Done.")


def _concat_wav_bytes(parts: list[bytes]) -> bytes:
    """複数のWAVバイト列を同じフォーマットに正規化して結合する."""
    from yomiage.exvoice.catalog import normalize_wav

    if not parts:
        return b""
    if len(parts) == 1:
        return parts[0]

    # 基準フォーマット = 最初のパーツ（TTS出力）
    with wave.open(io.BytesIO(parts[0]), "rb") as ref:
        ref_ch = ref.getnchannels()
        ref_width = ref.getsampwidth()
        ref_rate = ref.getframerate()

    out_buf = io.BytesIO()
    with wave.open(out_buf, "wb") as out_wav:
        out_wav.setnchannels(ref_ch)
        out_wav.setsampwidth(ref_width)
        out_wav.setframerate(ref_rate)
        for i, part in enumerate(parts):
            try:
                normalized = normalize_wav(part, target_sampwidth=ref_width)
                with wave.open(io.BytesIO(normalized), "rb") as w:
                    out_wav.writeframes(w.readframes(w.getnframes()))
            except Exception as e:
                logger.warning(f"Skipping malformed WAV part {i}: {e}")

    return out_buf.getvalue()


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")
    asyncio.run(main())
