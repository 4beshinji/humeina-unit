"""Pipeline — integrated text analysis + TTS synthesis pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from loguru import logger

from ..nlp.llm_backend import create_llm_backend
from ..nlp.pipeline import NLPAnalyzer
from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from ..tts.base import TTSParams
from .bridge import TTSBridge
from .models import AnalysisResult, PipelineChunk
from .profile_resolver import resolve_voice_profile

if TYPE_CHECKING:
    from ..tools.voice_profile import VoiceProfile
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

        # LLM-based scene analyzer (optional)
        llm_cfg = config.analyzer.llm
        try:
            llm = create_llm_backend(
                llm_cfg.backend,
                url=llm_cfg.url,
                api_key=llm_cfg.api_key,
                model=llm_cfg.model,
            )
        except Exception as e:
            logger.warning(f"LLM backend init failed, using rule-based only: {e}")
            llm = None

        # Unified NLP pipeline
        self._analyzer = NLPAnalyzer(
            llm=llm,
            max_chunk_chars=config.analyzer.max_chunk_chars,
        )

        # Parameter mapper
        self._param_mapper = ParamMapper(config.scene_params)

        # VoiceProfile (optional)
        self._voice_profile: "VoiceProfile | None" = None
        if config.voice_profile_name:
            self._voice_profile = resolve_voice_profile(
                config.voice_profile_name,
                search_dirs=[config.voice_profile_dir]
                if config.voice_profile_dir
                else None,
            )
            if self._voice_profile:
                logger.info(
                    f"Loaded voice profile: {self._voice_profile.display_name}"
                )

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

        # Analyze text through unified NLP pipeline
        chunk_analyses = await self._analyzer.analyze_text(
            text,
            character_db=self._character_db,
        )

        for chunk_analysis in chunk_analyses:
            chunk = chunk_analysis.chunk
            analyzed = chunk_analysis.segments

            # 支配的セグメントを選択
            dominant = max(analyzed, key=lambda s: len(s.text)) if analyzed else None
            if not dominant:
                continue

            # パラメータマッピング
            tts_params = self._param_mapper.map(dominant, self._character_db)

            # VoiceProfile があればキャラクターアーキタイプからパラメータを計算
            if self._voice_profile and self._character_db:
                tts_params = self._apply_voice_profile(
                    tts_params, dominant, self._character_db
                )

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

    def _apply_voice_profile(
        self,
        params: TTSParams,
        segment,
        character_db: CharacterDB,
    ) -> TTSParams:
        """VoiceProfile を使って TTSParams を上書き."""
        if not self._voice_profile or not segment.speaker:
            return params

        char = character_db.characters.get(segment.speaker)
        if not char or not char.base_params:
            return params

        archetype = char.base_params.get("_archetype")
        if not archetype or archetype not in self._voice_profile.presets:
            return params

        profile_params = self._voice_profile.compute_params(
            preset=archetype,
            emotion=segment.emotion,
            intensity=segment.intensity,
        )
        for key, value in profile_params.items():
            if key == "style_weights":
                params.style_weights = value
            elif hasattr(params, key):
                setattr(params, key, value)
        return params

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
