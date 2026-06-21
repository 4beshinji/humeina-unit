"""FastAPI server — HEMS Bridge + REST API."""

import asyncio
import io
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from .api.hooks import EventHooks
from .api.metrics import MetricsCollector
from .api.profile_resolver import resolve_voice_profile
from .batch.job_manager import BatchJobManager
from .config import get_tts_config, load_config
from .nlp.ollama_client import OllamaClient
from .nlp.scene_analyzer import SceneAnalyzer
from .nlp.splitter import TextSplitter
from .reader.engine import ReadingEngine
from .reader.param_mapper import ParamMapper
from .tts.manager import TTSManager
from .tts.voicepeak import VoicepeakProvider
from .tts.voicevox import VoicevoxProvider
from .tts.voisona import VoisonaProvider

_engine: ReadingEngine | None = None
_job_manager: BatchJobManager | None = None
_hooks: EventHooks | None = None
_metrics: MetricsCollector | None = None
_config: dict = {}


def _create_engine(config: dict) -> ReadingEngine:
    tts_cfg = config.get("tts", {})
    primary_name = tts_cfg.get("primary_provider", "voicevox")
    fallback_name = tts_cfg.get("fallback_provider")

    providers = {
        "voisona": lambda: VoisonaProvider(get_tts_config(config, "voisona")),
        "voicevox": lambda: VoicevoxProvider(get_tts_config(config, "voicevox")),
        "voicepeak": lambda: VoicepeakProvider(get_tts_config(config, "voicepeak")),
    }

    primary = providers.get(primary_name, providers["voicevox"])()
    fallback = None
    if fallback_name and fallback_name != primary_name and fallback_name in providers:
        fallback = providers[fallback_name]()

    # Load VoiceProfile if available
    voice_name = config.get("voisona", {}).get("default_voice")
    voice_profile = None
    if voice_name:
        voice_profile = resolve_voice_profile(voice_name)

    tts_manager = TTSManager(
        primary=primary,
        fallback=fallback,
        lookahead=tts_cfg.get("lookahead_chunks", 3),
        voice_profile=voice_profile,
        hooks=_hooks,
        metrics=_metrics,
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
    global _engine, _job_manager, _hooks, _metrics, _config
    from dotenv import load_dotenv

    load_dotenv()
    _config = load_config()
    _hooks = EventHooks()
    _metrics = MetricsCollector()
    _engine = _create_engine(_config)
    _job_manager = BatchJobManager(
        _config,
        output_dir=_config.get("batch", {}).get("output_dir", "output"),
        hooks=_hooks,
        metrics=_metrics,
    )
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
    voice_id: str | None = None
    speed: float = 1.0
    pitch: float = 0.0
    volume: float = 0.0
    intonation: float = 1.0
    preset: str | None = None
    emotion: str = "neutral"
    intensity: float = 0.5
    output_format: str = "wav"


class BatchSynthesizeRequest(BaseModel):
    url: str
    mode: str = "voicevox"
    output_format: str = "wav"
    video: bool = False
    style: str | None = None


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

    tts_params = TTSParams(
        voice_id=req.voice_id,
        speed=req.speed,
        pitch=req.pitch,
        volume=req.volume,
        intonation=req.intonation,
        preset=req.preset,
        emotion=req.emotion,
        intensity=req.intensity,
    )
    result = await _engine.tts.synthesize_immediate(req.text, tts_params)

    if req.output_format != "wav":
        result = result.convert(req.output_format)

    return StreamingResponse(
        io.BytesIO(result.audio_data),
        media_type=f"audio/{req.output_format}",
        headers={
            "X-Audio-Duration": str(result.duration or 0.0),
            "X-Audio-Format": result.format,
        },
    )


@app.get("/api/yomiage/voices")
async def list_voices(provider: str | None = Query(None)):
    """利用可能なボイス一覧を返す."""
    if not _engine:
        raise HTTPException(503, "Engine not initialized")

    tts = _engine.tts
    if provider and tts.fallback and provider == tts.fallback.name:
        target = tts.fallback
    else:
        target = tts.primary

    voices = await target.list_voices()
    return {
        "engine": target.name,
        "voices": [
            {
                "id": str(v.get("id", "")),
                "name": v.get("name", v.get("label", "")),
            }
            for v in voices
        ],
    }


@app.post("/api/yomiage/synthesize/batch")
async def synthesize_batch(req: BatchSynthesizeRequest):
    """URL からバッチ合成ジョブを開始する."""
    if not _job_manager:
        raise HTTPException(503, "Job manager not initialized")

    job = _job_manager.create_job(
        url=req.url,
        mode=req.mode,
        output_format=req.output_format,
        video=req.video,
        style=req.style,
    )
    asyncio.create_task(
        _job_manager.run_job(
            job.id,
            output_format=req.output_format,
            video=req.video,
            style=req.style,
        )
    )
    return {"job_id": job.id, "status": job.status}


@app.get("/api/yomiage/synthesize/batch/{job_id}")
async def get_batch_job(job_id: str):
    """ジョブ状態を取得する."""
    if not _job_manager:
        raise HTTPException(503, "Job manager not initialized")
    job = _job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/api/yomiage/synthesize/batch/{job_id}/progress")
async def batch_job_progress(job_id: str):
    """SSE でジョブ進捗を配信する."""
    if not _job_manager:
        raise HTTPException(503, "Job manager not initialized")

    async def event_stream():
        queue = _job_manager.subscribe(job_id)
        try:
            while True:
                event = await queue.get()
                yield f"event: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["status"] in ("completed", "failed"):
                    break
        finally:
            _job_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/yomiage/metrics")
async def get_metrics():
    """収集済みメトリクスを返す."""
    if not _metrics:
        raise HTTPException(503, "Metrics not initialized")
    return _metrics.to_dict()


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
