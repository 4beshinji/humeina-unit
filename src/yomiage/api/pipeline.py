"""Pipeline — integrated text analysis + TTS synthesis pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from loguru import logger

from ..nlp.classifier import TextClassifier
from ..nlp.llm_backend import create_llm_backend
from ..nlp.scene_analyzer import AnalyzedSegment, SceneAnalyzer
from ..nlp.speaker import SpeakerExtractor
from ..nlp.splitter import TextSplitter
from ..nlp.text_processor import TextProcessor
from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from ..tts.base import TTSParams
from .bridge import TTSBridge
from .models import AnalysisResult, PipelineChunk

if TYPE_CHECKING:
    from .config import PipelineConfig


class Pipeline:
    """テキスト→分析→パラメータマッピング→音声合成の統合パイプライン.

    Usage::

        from yomiage import Pipeline, PipelineConfig, TTSEngineConfig

        config = PipelineConfig(tts=TTSEngineConfig(engine="voicevox"))
        pipeline = Pipeline(config)
        chunks = await pipeline.process("テキスト...")
    """

    def __init__(self, config: PipelineConfig):
        self._config = config

        # TTS Bridge
        self._bridge = TTSBridge.from_config(config.tts, config.tts_fallback)

        # NLP components
        self._processor = TextProcessor()
        self._splitter = TextSplitter(
            max_chars=config.analyzer.max_chunk_chars
        )
        self._classifier = TextClassifier()
        self._speaker_extractor = SpeakerExtractor()

        # LLM-based scene analyzer (optional)
        llm_cfg = config.analyzer.llm
        try:
            backend = create_llm_backend(
                llm_cfg.backend,
                url=llm_cfg.url,
                api_key=llm_cfg.api_key,
                model=llm_cfg.model,
            )
            self._scene_analyzer: SceneAnalyzer | None = SceneAnalyzer(backend)
        except Exception as e:
            logger.warning(f"LLM backend init failed, using rule-based only: {e}")
            self._scene_analyzer = None

        # Parameter mapper
        self._param_mapper = ParamMapper(config.scene_params)

        # Character DB (in-memory, no file persistence)
        self._character_db: CharacterDB | None = None

    @classmethod
    def create(cls, engine: str, **kwargs: object) -> Pipeline:
        """最小構成で作成.

        Args:
            engine: "voicevox", "voisona", or "voicepeak"
            **kwargs: PipelineConfig fields (url, model, etc.)
        """
        from .config import PipelineConfig, TTSEngineConfig

        tts_kwargs = {}
        for k in ("url", "username", "password", "default_voice"):
            if k in kwargs:
                tts_kwargs[k] = kwargs.pop(k)

        tts = TTSEngineConfig(engine=engine, **tts_kwargs)  # type: ignore[arg-type]
        config = PipelineConfig(tts=tts, **kwargs)  # type: ignore[arg-type]
        return cls(config)

    async def process(
        self,
        text: str,
        *,
        characters: dict[str, dict] | None = None,
    ) -> list[PipelineChunk]:
        """バッチモード: テキスト全体を処理して結果を返す."""
        results: list[PipelineChunk] = []
        async for chunk in self.stream(text, characters=characters):
            results.append(chunk)
        return results

    async def stream(
        self,
        text: str,
        *,
        characters: dict[str, dict] | None = None,
    ) -> AsyncIterator[PipelineChunk]:
        """ストリーミングモード: チャンクごとにyield."""
        # Character DB setup
        self._character_db = CharacterDB("pipeline", persist=False)
        if characters:
            for name, profile in characters.items():
                profile_with_name = {"name": name, **profile}
                self._character_db.get_or_create(name, profile_hint=profile_with_name)

        # テキスト前処理
        clean = self._processor.process(text)
        if not clean:
            return

        # チャンク分割
        chunks = self._splitter.split(clean)

        for chunk in chunks:
            if chunk.is_scene_break or not chunk.text.strip():
                continue

            # NLP分析
            segments = self._classifier.classify(chunk.text)
            segments = self._speaker_extractor.extract(segments)

            if self._scene_analyzer:
                analyzed = await self._scene_analyzer.analyze_batch(
                    segments,
                    known_characters=(
                        self._character_db.known_names
                        if self._character_db
                        else None
                    ),
                )
                # 新キャラクター検出
                for seg in analyzed:
                    if seg.new_character and self._character_db:
                        self._character_db.get_or_create(
                            seg.new_character.get("name", ""),
                            profile_hint=seg.new_character,
                        )
                    if seg.speaker and self._character_db:
                        self._character_db.get_or_create(seg.speaker)
            else:
                analyzed = [
                    AnalyzedSegment.from_segment(
                        seg,
                        speaker=(
                            seg.speaker_candidates[0]
                            if seg.speaker_candidates
                            else None
                        ),
                    )
                    for seg in segments
                ]

            # 支配的セグメントを選択
            dominant = (
                max(analyzed, key=lambda s: len(s.text)) if analyzed else None
            )
            if not dominant:
                continue

            # パラメータマッピング
            tts_params = self._param_mapper.map(dominant, self._character_db)

            # TTS合成
            synth_result = await self._bridge.synthesize(
                chunk.text, params=tts_params
            )

            # 結果を構築
            analysis = AnalysisResult(
                text=dominant.text,
                segment_type=dominant.type.value,
                speaker=dominant.speaker,
                scene=dominant.scene,
                emotion=dominant.emotion,
                intensity=dominant.intensity,
            )

            yield PipelineChunk(
                text=chunk.text,
                analysis=analysis,
                audio=synth_result,
                tts_params=_params_to_dict(tts_params),
            )

    # --- sync ラッパー ---

    def process_sync(
        self, text: str, **kwargs: object
    ) -> list[PipelineChunk]:
        """process の同期版."""
        return asyncio.run(self.process(text, **kwargs))


def _params_to_dict(params: TTSParams) -> dict:
    """TTSParamsをdictに変換（デフォルト値は省略）."""
    d: dict = {}
    if params.voice_id:
        d["voice_id"] = params.voice_id
    if params.speed != 1.0:
        d["speed"] = params.speed
    if params.pitch != 0.0:
        d["pitch"] = params.pitch
    if params.volume != 0.0:
        d["volume"] = params.volume
    if params.intonation != 1.0:
        d["intonation"] = params.intonation
    if params.huskiness != 0.0:
        d["huskiness"] = params.huskiness
    if params.alp != 0.0:
        d["alp"] = params.alp
    if params.style_weights:
        d["style_weights"] = params.style_weights
    return d
