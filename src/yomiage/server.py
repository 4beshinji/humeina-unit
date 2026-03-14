"""FastAPI server — HEMS Bridge + REST API."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel

from .config import get_tts_config, load_config
from .nlp.ollama_client import OllamaClient
from .nlp.scene_analyzer import SceneAnalyzer
from .nlp.splitter import TextSplitter
from .reader.engine import ReadingEngine
from .reader.param_mapper import ParamMapper
from .tts.manager import TTSManager
from .tts.voicevox import VoicevoxProvider
from .tts.voisona import VoisonaProvider

_engine: ReadingEngine | None = None
_config: dict = {}


def _create_engine(config: dict) -> ReadingEngine:
    tts_cfg = config.get("tts", {})
    primary_name = tts_cfg.get("primary_provider", "voicevox")
    fallback_name = tts_cfg.get("fallback_provider")

    providers = {
        "voisona": lambda: VoisonaProvider(get_tts_config(config, "voisona")),
        "voicevox": lambda: VoicevoxProvider(get_tts_config(config, "voicevox")),
    }

    primary = providers.get(primary_name, providers["voicevox"])()
    fallback = None
    if fallback_name and fallback_name != primary_name and fallback_name in providers:
        fallback = providers[fallback_name]()

    tts_manager = TTSManager(
        primary=primary,
        fallback=fallback,
        lookahead=tts_cfg.get("lookahead_chunks", 3),
    )

    ollama_cfg = config.get("ollama", {})
    ollama = OllamaClient(
        url=ollama_cfg.get("url", "http://localhost:11434"),
        model=ollama_cfg.get("model", "qwen3.5:3b"),
    )

    from pathlib import Path

    scene_config_path = Path("config/scene_params.yaml")
    param_mapper = ParamMapper.from_config_file(scene_config_path)

    return ReadingEngine(
        tts_manager=tts_manager,
        splitter=TextSplitter(max_chars=tts_cfg.get("max_chunk_chars", 200)),
        scene_analyzer=SceneAnalyzer(ollama),
        param_mapper=param_mapper,
        auto_advance=config.get("reader", {}).get("auto_advance", True),
        lookahead_chunks=tts_cfg.get("lookahead_chunks", 5),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _config
    from dotenv import load_dotenv

    load_dotenv()
    _config = load_config()
    _engine = _create_engine(_config)
    logger.info("Yomiage server started")
    yield
    if _engine and _engine.is_running:
        _engine.stop()
    logger.info("Yomiage server stopped")


app = FastAPI(title="Yomiage Server", version="0.1.0", lifespan=lifespan)


class ReadRequest(BaseModel):
    url: str
    provider: str | None = None


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = "neutral"
    speed: float = 1.0


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "reading": _engine.is_running if _engine else False,
    }


@app.get("/api/yomiage/status")
async def status():
    if not _engine:
        raise HTTPException(503, "Engine not initialized")
    return _engine.current_status


@app.post("/api/yomiage/read")
async def read(req: ReadRequest):
    if not _engine:
        raise HTTPException(503, "Engine not initialized")
    if _engine.is_running:
        raise HTTPException(409, "Already reading")
    asyncio.create_task(_engine.read_url(req.url))
    return {"status": "started", "url": req.url}


@app.post("/api/yomiage/pause")
async def pause():
    if _engine and _engine.is_running:
        _engine.pause()
        return {"status": "paused"}
    raise HTTPException(409, "Not reading")


@app.post("/api/yomiage/resume")
async def resume():
    if _engine and _engine.is_paused:
        _engine.resume()
        return {"status": "resumed"}
    raise HTTPException(409, "Not paused")


@app.post("/api/yomiage/stop")
async def stop():
    if _engine and _engine.is_running:
        _engine.stop()
        return {"status": "stopped"}
    raise HTTPException(409, "Not reading")


@app.post("/api/yomiage/synthesize")
async def synthesize(req: SynthesizeRequest):
    if not _engine:
        raise HTTPException(503, "Engine not initialized")
    from .tts.base import TTSParams

    result = await _engine.tts.synthesize_immediate(
        req.text, TTSParams(speed=req.speed)
    )
    return {
        "duration": result.duration,
        "format": result.format,
        "has_audio": len(result.audio_data) > 0,
    }


@app.post("/api/yomiage/news")
async def news_daily():
    if not _engine:
        raise HTTPException(503, "Engine not initialized")
    from .news.fetcher import NewsFetcher
    from .news.summarizer import NewsSummarizer

    ollama_cfg = _config.get("ollama", {})
    ollama = OllamaClient(
        url=ollama_cfg.get("url", "http://localhost:11434"),
        model=ollama_cfg.get("summary_model") or ollama_cfg.get("model", "qwen3.5:3b"),
    )

    news_cfg = _config.get("news", {})
    fetcher = NewsFetcher(news_cfg.get("sources"))
    summarizer = NewsSummarizer(ollama)

    articles = await fetcher.fetch_all()
    summary = await summarizer.daily_summary(articles)

    from .tts.base import TTSParams

    await _engine.tts.start()
    await _engine.tts.enqueue(summary, TTSParams())
    await _engine.tts.drain()

    return {"status": "reading", "article_count": len(articles)}
