"""News scheduler — periodic fetching and reading."""

import asyncio

from loguru import logger

from ..nlp.ollama_client import OllamaClient
from ..nlp.text_processor import TextProcessor
from ..tts.base import TTSParams
from ..tts.manager import TTSManager
from .fetcher import NewsFetcher
from .summarizer import NewsSummarizer, split_by_category
from .urgency import UrgencyDetector


class NewsScheduler:
    """ニューススケジューラ.

    - 日次サマリ: 毎朝指定時刻
    - 速報チェック: N分間隔ポーリング
    """

    def __init__(
        self,
        tts_manager: TTSManager,
        ollama: OllamaClient,
        sources: list[str] | None = None,
        daily_schedule: str = "08:00",
        poll_interval_minutes: int = 5,
        urgency_threshold: float = 0.8,
        tts_speed: float = 1.0,
    ):
        self.tts = tts_manager
        self.ollama = ollama
        self.processor = TextProcessor()
        self.tts_speed = tts_speed
        self.fetcher = NewsFetcher(sources)
        self.summarizer = NewsSummarizer(ollama)
        self.urgency = UrgencyDetector(ollama, urgency_threshold)
        self.daily_schedule = daily_schedule
        self.poll_interval = poll_interval_minutes * 60
        self._seen_urls: set[str] = set()
        self._running = False

    async def run_daily_summary(self) -> None:
        """日次サマリを生成・読み上げ."""
        logger.info("Generating daily news summary")
        articles = await self.fetcher.fetch_all()
        summary = await self.summarizer.daily_summary(articles)

        summary = self.processor.process(summary)
        chunks = split_by_category(summary)
        logger.info(f"Daily summary: {len(summary)} chars, {len(chunks)} categories")

        params = TTSParams(speed=self.tts_speed)
        await self.tts.start()
        for chunk in chunks:
            if chunk.strip():
                await self.tts.enqueue(chunk, params)
        await self.tts.drain()

    async def check_urgent(self) -> None:
        """速報チェック."""
        articles = await self.fetcher.fetch_all()
        for article in articles:
            if article.url in self._seen_urls:
                continue
            self._seen_urls.add(article.url)

            score = await self.urgency.score(article)
            if self.urgency.is_urgent(score):
                logger.info(f"Urgent news (score={score:.2f}): {article.title}")
                text = await self.summarizer.translate_if_needed(article)
                text = self.processor.process(f"速報です。{text}")
                if TextProcessor.has_alphabet(text):
                    text = await self.ollama.romanize(text)
                await self.tts.start()
                await self.tts.enqueue(text, TTSParams(speed=self.tts_speed))
                await self.tts.drain()

    async def start_polling(self) -> None:
        """ポーリングループ開始."""
        self._running = True
        logger.info(f"News polling started (interval={self.poll_interval}s)")
        while self._running:
            try:
                await self.check_urgent()
            except Exception as e:
                logger.error(f"News poll error: {e}")
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False
