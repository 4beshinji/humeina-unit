"""Pipeline — integrated text analysis + TTS synthesis pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from loguru import logger

from ..application.planning import UtterancePlanner
from ..application.speech_text import render_speech_text
from ..application.synthesis import SynthesisRequest, SynthesisService
from ..application.voice_parameters import resolve_voice_parameters
from ..core.models import VoiceIntent
from ..core.text import TextDocument
from ..nlp.llm_backend import create_llm_backend
from ..nlp.pipeline import NLPAnalyzer
from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from ..tts.base import TTSParams
from .bridge import TTSBridge, _to_api_synthesis_result
from .models import AnalysisResult, PipelineChunk
from .profile_resolver import resolve_voice_profile

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

    def __init__(
        self,
        config: PipelineConfig,
        *,
        synthesis_service: SynthesisService | None = None,
    ):
        self._config = config

        self._voice_profile = (
            resolve_voice_profile(
                config.voice_profile_name,
                search_dirs=[config.voice_profile_dir] if config.voice_profile_dir else None,
            )
            if config.voice_profile_name
            else None
        )

        # TTS Bridge
        if synthesis_service is None:
            self._bridge = TTSBridge.from_config(
                config.tts, config.tts_fallback, voice_profile=self._voice_profile
            )
        else:
            self._bridge = TTSBridge(synthesis_service)
        self._synthesis_service = self._bridge.service

        # LLM-based scene analyzer (optional)
        llm_cfg = config.analyzer.llm
        try:
            llm = (
                None
                if config.analyzer.analysis.llm_policy == "off"
                else create_llm_backend(
                    llm_cfg.backend,
                    url=llm_cfg.url,
                    api_key=llm_cfg.api_key,
                    model=llm_cfg.model,
                )
            )
        except Exception as e:
            logger.warning(f"LLM backend init failed, using rule-based only: {e}")
            llm = None

        # Unified NLP pipeline
        self._analyzer = NLPAnalyzer(
            llm=llm,
            max_chunk_chars=config.analyzer.max_chunk_chars,
            analysis_settings=config.analyzer.analysis,
        )

        # Parameter mapper
        self._param_mapper = (
            ParamMapper(config.scene_params)
            if config.scene_params is not None
            else ParamMapper.from_config_file()
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

        synthesis_service = kwargs.pop("synthesis_service", None)
        tts_kwargs = {}
        for k in ("url", "username", "password", "default_voice"):
            if k in kwargs:
                tts_kwargs[k] = kwargs.pop(k)

        tts = TTSEngineConfig(engine=engine, **tts_kwargs)  # type: ignore[arg-type]
        config = PipelineConfig(tts=tts, **kwargs)  # type: ignore[arg-type]
        return cls(config, synthesis_service=synthesis_service)  # type: ignore[arg-type]

    @property
    def synthesis_service(self) -> SynthesisService:
        """Pipelineが共有する合成serviceを返す。"""

        return self._synthesis_service

    async def process(
        self,
        text: str | TextDocument,
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
        text: str | TextDocument,
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

        plan = await UtterancePlanner(self._analyzer).from_text(
            text,
            known_characters=self._character_db.known_names,
            character_db=self._character_db,
        )
        self.last_plan = plan
        for utterance in plan.utterances:
            character = self._character_db.characters.get(utterance.speaker_id)
            intent = VoiceIntent(
                archetype=character.personality if character else None,
                gender=character.gender if character else None,
                age_group=character.age_group if character else None,
                preferred_voice_profile=character.voice_id if character else None,
                emotion=utterance.emotion,
                intensity=utterance.intensity,
            )
            context = self._param_mapper.context_for(
                utterance,
                self._character_db,
                voice_profile=self._voice_profile,
                provider=self._bridge.engine_name,
            )
            resolved = resolve_voice_parameters(
                intent,
                profile=context.profile,
                scene=context.scene,
                emotion=context.emotion,
            )
            result = await self._bridge.service.synthesize(
                SynthesisRequest(
                    text=render_speech_text(utterance),
                    voice_intent=intent,
                    utterance_id=utterance.id,
                    scene=utterance.scene,
                    parameter_context=context,
                )
            )
            yield PipelineChunk(
                text=utterance.text,
                analysis=AnalysisResult(
                    text=utterance.text,
                    segment_type=utterance.segment_type.value,
                    speaker=utterance.speaker_id,
                    scene=utterance.scene,
                    emotion=utterance.emotion.value,
                    intensity=utterance.intensity,
                ),
                audio=_to_api_synthesis_result(result),
                tts_params={
                    "speed": resolved.speaking_rate,
                    "pitch": resolved.pitch,
                    "volume": resolved.volume,
                    "intonation": resolved.intonation,
                },
            )

    # --- sync ラッパー ---

    def process_sync(self, text: str, **kwargs: object) -> list[PipelineChunk]:
        """process の同期版."""
        return asyncio.run(self.process(text, **kwargs))

    async def close(self) -> None:
        """内部で生成したSDK service/providerを解放する。"""

        await self._bridge.close()
        scene = self._analyzer.scene_analyzer
        close = getattr(scene._backend, "close", None) if scene else None
        if callable(close):
            await close()

    async def __aenter__(self) -> "Pipeline":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()


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
