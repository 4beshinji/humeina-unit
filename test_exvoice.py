"""EXボイス機能の動作確認スクリプト.

青空文庫のテキストを実際に取得・分析し、
どのチャンクにどのクリップが挿入されるかを表示する（TTS再生なし）。
"""

import asyncio
from pathlib import Path

from loguru import logger

AOZORA_URL = "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"  # 芥川龍之介「羅生門」
WAV_DIR = Path("/home/sin/code/una/VoiceWav")


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

    # ── セットアップ ──────────────────────────────────
    logger.info(f"Fetching: {AOZORA_URL}")
    source = AozoraSource()
    chapter = await source.fetch_chapter(AOZORA_URL)
    logger.info(f"Title: {chapter.title}")

    processor = TextProcessor()
    clean = processor.process(chapter.text)
    logger.info(f"Text length: {len(clean)} chars")

    splitter = TextSplitter(max_chars=200)
    chunks = splitter.split(clean)
    speakable = [c for c in chunks if c.text.strip() and not c.is_scene_break]
    logger.info(f"Chunks: {len(speakable)} speakable / {len(chunks)} total")

    # ── NLP ──────────────────────────────────────────
    backend = OllamaBackend(model="qwen3:8b")
    classifier = TextClassifier()
    speaker = SpeakerExtractor()
    analyzer = SceneAnalyzer(backend)

    analyzed_cache: dict = {}
    WINDOW = 12  # テスト用ウィンドウサイズ

    logger.info(f"Analyzing first {WINDOW} chunks...")
    window = speakable[:WINDOW]
    for chunk in window:
        segs = classifier.classify(chunk.text)
        segs = speaker.extract(segs)
        analyzed = await analyzer.analyze_batch(segs)
        analyzed_cache[chunk.index] = analyzed

    # ── EXボイス ─────────────────────────────────────
    catalog = load_catalog(WAV_DIR)
    logger.info(f"Catalog: {len(catalog)} clips")

    ex_backend = OllamaBackend(model="qwen3:8b")
    selector = ExVoiceSelector(ex_backend, catalog)
    manager = ExVoiceManager(
        catalog=catalog,
        selector=selector,
        cooldown_chunks=3,   # テスト用に短くする
        max_per_chapter=10,
        llm_max_insertions=3,
    )
    manager.reset_chapter()

    logger.info("Running ExVoice analyze_window...")
    await manager.analyze_window(window, analyzed_cache)

    # ── 結果表示 ─────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"対象: {chapter.title}")
    print("=" * 70)

    inserted_count = 0
    for chunk in window:
        segs = analyzed_cache.get(chunk.index, [])
        scene = segs[0].scene if segs else "?"
        emotion = segs[0].emotion if segs else "?"
        text_preview = chunk.text[:50].replace("\n", " ")
        print(f"\n[{chunk.index:03d}] ({scene}/{emotion}) {text_preview}…")

        # pop_clips_for は cooldown チェックを行うため順番に呼ぶ
        clips = manager.pop_clips_for(chunk.index)
        for clip in clips:
            inserted_count += 1
            print(f"  ▶ EX voice → {clip.clip_id}: 「{clip.text}」")

    print("\n" + "=" * 70)
    print(f"挿入クリップ総数: {inserted_count} / {len(window)} チャンク")
    print("=" * 70)


if __name__ == "__main__":
    import sys
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")
    asyncio.run(main())
