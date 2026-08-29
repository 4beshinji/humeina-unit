"""TTSBridge — unified interface to multiple TTS engines."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from ..application.synthesis import SynthesisFailure, SynthesisRequest, SynthesisService
from ..application.voice_parameters import VoiceOverrides, VoiceResolutionContext
from ..bootstrap import create_synthesis_service, create_tts_provider
from ..core.models import VoiceIntent
from ..tts.base import AudioResult, TTSParams, TTSProvider
from ..tts.cache import TTSCache
from ..tts.retry import RetryConfig
from .hooks import EventHooks
from .long_text import LongTextOptions, synthesize_long_text
from .metrics import MetricsCollector
from .models import SynthesisResult, VoiceInfo

# Backwards-compatible patch point for callers/tests that used to monkeypatch the bridge
# factory.  The implementation itself now lives in the composition root.
create_provider = create_tts_provider

if TYPE_CHECKING:
    from ..tools.voice_profile import VoiceProfile

if TYPE_CHECKING:
    from .config import TTSEngineConfig


class TTSBridge:
    """複数TTSエンジンを統一的に扱うブリッジ.

    Usage::

        bridge = TTSBridge.create("voicevox", url="http://localhost:50021")
        result = await bridge.synthesize("こんにちは", voice_id="47")
        result.save("output.wav")
    """

    def __init__(
        self,
        provider: TTSProvider | SynthesisService,
        fallback: TTSProvider | None = None,
        retry_config: RetryConfig | None = None,
        cache: TTSCache | None = None,
        voice_profile: "VoiceProfile | None" = None,
        hooks: EventHooks | None = None,
        metrics: MetricsCollector | None = None,
        *,
        service: SynthesisService | None = None,
    ):
        self._provider = provider
        self._fallback = fallback
        self._retry_config = retry_config
        self._cache = cache
        self._voice_profile = voice_profile
        self._hooks = hooks
        self._metrics = metrics
        if service is not None:
            self._service = service
            self._owns_service = False
            self._attach_metrics(service)
            if isinstance(provider, SynthesisService):
                self._provider = provider.primary_provider
                self._fallback = provider.fallback_provider
        elif isinstance(provider, SynthesisService):
            self._service = provider
            self._owns_service = False
            self._attach_metrics(provider)
            self._provider = provider.primary_provider
            self._fallback = provider.fallback_provider
        else:
            providers: dict[str, object] = {provider.name: provider}
            if fallback is not None:
                providers[fallback.name] = fallback
            self._service = create_synthesis_service(
                providers,
                primary=provider.name,
                fallback=fallback.name if fallback is not None else None,
                cache=cache,
                retry_policy=retry_config,
                metrics=metrics,
            )
            self._owns_service = True

    def _attach_metrics(self, service: SynthesisService) -> None:
        """Attach the facade metrics port only when the service has none.

        ``SynthesisService`` is the single synthesis observability boundary.  A service
        supplied by a composition root may already own a collector; replacing it here
        would either disconnect the root collector or make ownership ambiguous.
        """

        if self._metrics is None or getattr(service, "metrics", None) is not None:
            return
        try:
            service.metrics = self._metrics
        except (AttributeError, TypeError):
            # Third-party service implementations may expose a read-only metrics port.
            # Hooks remain available even when metrics cannot be attached.
            return

    @property
    def service(self) -> SynthesisService:
        """共有application serviceを返す（SDK factory/統合入口向け）。"""

        return self._service

    @classmethod
    def create(
        cls,
        engine: str,
        *,
        retry_config: RetryConfig | None = None,
        cache: TTSCache | None = None,
        voice_profile: "VoiceProfile | None" = None,
        hooks: EventHooks | None = None,
        metrics: MetricsCollector | None = None,
        **kwargs: object,
    ) -> "TTSBridge":
        """ファクトリ: エンジン名+パラメータで作成.

        Args:
            engine: "voicevox", "voisona", or "voicepeak"
            retry_config: リトライ設定
            **kwargs: url, username, password, default_voice etc.
        """
        from .config import TTSEngineConfig

        config = TTSEngineConfig(engine=engine, **kwargs)  # type: ignore[arg-type]
        provider = create_provider(config)
        return cls(
            provider,
            retry_config=retry_config,
            cache=cache,
            voice_profile=voice_profile,
            hooks=hooks,
            metrics=metrics,
        )

    @classmethod
    def from_config(
        cls,
        config: "TTSEngineConfig",
        fallback: "TTSEngineConfig | None" = None,
        *,
        retry_config: RetryConfig | None = None,
        cache: TTSCache | None = None,
        voice_profile: "VoiceProfile | None" = None,
        hooks: EventHooks | None = None,
        metrics: MetricsCollector | None = None,
    ) -> "TTSBridge":
        """TTSEngineConfigから作成."""
        provider = create_provider(config)
        fb = create_provider(fallback) if fallback else None
        return cls(
            provider,
            fb,
            retry_config=retry_config,
            cache=cache,
            voice_profile=voice_profile,
            hooks=hooks,
            metrics=metrics,
        )

    @classmethod
    def from_provider(
        cls,
        provider: TTSProvider,
        fallback: TTSProvider | None = None,
        *,
        retry_config: RetryConfig | None = None,
        cache: TTSCache | None = None,
        voice_profile: "VoiceProfile | None" = None,
        hooks: EventHooks | None = None,
        metrics: MetricsCollector | None = None,
    ) -> "TTSBridge":
        """既存のTTSProviderインスタンスから作成."""
        return cls(
            provider,
            fallback,
            retry_config=retry_config,
            cache=cache,
            voice_profile=voice_profile,
            hooks=hooks,
            metrics=metrics,
        )

    @classmethod
    def from_service(
        cls,
        service: SynthesisService,
        *,
        voice_profile: "VoiceProfile | None" = None,
        hooks: EventHooks | None = None,
        metrics: MetricsCollector | None = None,
    ) -> "TTSBridge":
        """既にcomposition rootで生成したserviceをSDK facadeへ接続する。"""

        return cls(
            service,
            voice_profile=voice_profile,
            hooks=hooks,
            metrics=metrics,
        )

    @property
    def engine_name(self) -> str:
        """現在のプライマリエンジン名."""
        return self._service.primary_provider.name

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        speed: float = 1.0,
        pitch: float = 0.0,
        volume: float = 0.0,
        intonation: float = 1.0,
        preset: str | None = None,
        emotion: str = "neutral",
        intensity: float = 0.5,
        params: TTSParams | None = None,
        **kwargs: object,
    ) -> SynthesisResult:
        """テキストを音声合成.

        VoiSonaなど audio_data が空のプロバイダーは
        synthesize_to_file 経由で一時ファイルからバイト列を取得する。
        preset/emotion/intensity を指定すると VoiceProfile 経由で
        パラメータを計算する。
        """
        synth_kwargs = _build_synth_kwargs(
            voice_id=voice_id,
            speed=speed,
            pitch=pitch,
            volume=volume,
            intonation=intonation,
            params=params,
            voice_profile=self._voice_profile,
            preset=preset,
            emotion=emotion,
            intensity=intensity,
            **kwargs,
        )
        _add_intent_kwargs(synth_kwargs, preset=preset, emotion=emotion, intensity=intensity)
        if params is not None:
            _add_intent_kwargs(
                synth_kwargs,
                preset=params.preset,
                emotion=params.emotion,
                intensity=params.intensity,
            )

        engine_name = self.engine_name
        if self._hooks:
            self._hooks.emit_synthesis_start(text, engine_name, synth_kwargs)

        start = asyncio.get_event_loop().time()
        try:
            result = await self._service.synthesize(
                SynthesisRequest(
                    text=text,
                    voice_intent=_voice_intent_from_kwargs(synth_kwargs),
                    parameter_context=VoiceResolutionContext(
                        overrides=VoiceOverrides.from_mapping(synth_kwargs)
                    ),
                    metadata=synth_kwargs,
                )
            )

            cache_hit = result.cache_hit
            if cache_hit and self._hooks:
                self._hooks.emit_cache_hit(text, engine_name, synth_kwargs)

            duration_ms = (asyncio.get_event_loop().time() - start) * 1000
            if self._hooks:
                self._hooks.emit_synthesis_end(
                    text, engine_name, synth_kwargs, duration_ms, cache_hit=cache_hit
                )

            return _to_api_synthesis_result(result)
        except SynthesisFailure as exc:
            # The legacy facade historically exposed the provider's actionable message.  Keep
            # that behavior while retaining the typed failure (and metadata) internally.
            message = str(exc)
            errors = exc.metadata.get("errors")
            if isinstance(errors, list) and errors:
                last = errors[-1]
                if isinstance(last, dict) and last.get("message"):
                    message = str(last["message"])
            if self._hooks:
                self._hooks.emit_synthesis_error(
                    text, engine_name, synth_kwargs, message
                )
            raise RuntimeError(message) from exc
        except Exception as exc:
            if self._hooks:
                self._hooks.emit_synthesis_error(
                    text, engine_name, synth_kwargs, str(exc)
                )
            raise

    async def synthesize_to_file(
        self,
        text: str,
        path: str | Path,
        *,
        voice_id: str | None = None,
        speed: float = 1.0,
        pitch: float = 0.0,
        preset: str | None = None,
        emotion: str = "neutral",
        intensity: float = 0.5,
        params: TTSParams | None = None,
        **kwargs: object,
    ) -> SynthesisResult:
        """テキストを音声合成しファイルに保存."""
        synth_kwargs = _build_synth_kwargs(
            voice_id=voice_id,
            speed=speed,
            pitch=pitch,
            params=params,
            voice_profile=self._voice_profile,
            preset=preset,
            emotion=emotion,
            intensity=intensity,
            **kwargs,
        )
        _add_intent_kwargs(synth_kwargs, preset=preset, emotion=emotion, intensity=intensity)
        if params is not None:
            _add_intent_kwargs(
                synth_kwargs,
                preset=params.preset,
                emotion=params.emotion,
                intensity=params.intensity,
            )
        try:
            result = await self._service.synthesize(
                SynthesisRequest(
                    text=text,
                    voice_intent=_voice_intent_from_kwargs(synth_kwargs),
                    parameter_context=VoiceResolutionContext(
                        overrides=VoiceOverrides.from_mapping(synth_kwargs)
                    ),
                    destination=str(path),
                    metadata=synth_kwargs,
                )
            )
        except SynthesisFailure as exc:
            message = str(exc)
            errors = exc.metadata.get("errors")
            if isinstance(errors, list) and errors:
                last = errors[-1]
                if isinstance(last, dict) and last.get("message"):
                    message = str(last["message"])
            raise RuntimeError(message) from exc
        return _to_api_synthesis_result(result)

    async def synthesize_long(
        self,
        text: str,
        *,
        max_chars: int = 200,
        pause_between_chunks: float = 0.0,
        **kwargs: object,
    ) -> SynthesisResult:
        """長文を自動分割して合成.

        Args:
            text: 合成する長文
            max_chars: 1チャンクあたりの最大文字数
            pause_between_chunks: チャンク間の無音秒数
            **kwargs: bridge.synthesize() に渡すパラメータ
        """
        options = LongTextOptions(
            max_chars=max_chars,
            pause_between_chunks=pause_between_chunks,
        )
        return await synthesize_long_text(
            self, text, options=options, **kwargs
        )

    async def list_voices(self) -> list[VoiceInfo]:
        """利用可能なボイス一覧を返す."""
        raw_voices = await self._service.list_voices()
        return [
            VoiceInfo(
                id=v.id,
                name=v.name,
                engine=v.provider or self.engine_name,
                gender=v.metadata.get("gender"),
                age_group=v.metadata.get("age_group"),
                extra={
                    key: value
                    for key, value in v.metadata.items()
                    if key not in {"gender", "age_group"}
                },
            )
            for v in raw_voices
        ]

    async def is_available(self) -> bool:
        """プロバイダーが利用可能か確認."""
        return (await self._service.health()).healthy

    async def close(self) -> None:
        """Bridgeが所有するservice/providerを解放する。"""

        if self._owns_service:
            await self._service.close()

    async def __aenter__(self) -> "TTSBridge":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    # --- sync ラッパー ---

    def synthesize_sync(self, text: str, **kwargs: object) -> SynthesisResult:
        """synthesize の同期版."""
        return asyncio.run(self.synthesize(text, **kwargs))

    def list_voices_sync(self) -> list[VoiceInfo]:
        """list_voices の同期版."""
        return asyncio.run(self.list_voices())

    def is_available_sync(self) -> bool:
        """is_available の同期版."""
        return asyncio.run(self.is_available())

def _build_synth_kwargs(
    *,
    voice_id: str | None = None,
    speed: float = 1.0,
    pitch: float = 0.0,
    volume: float = 0.0,
    intonation: float = 1.0,
    params: TTSParams | None = None,
    voice_profile: "VoiceProfile | None" = None,
    preset: str | None = None,
    emotion: str = "neutral",
    intensity: float = 0.5,
    **extra: object,
) -> dict:
    """synthesize()呼び出し用のkwargsを構築."""
    kwargs: dict = {}

    # VoiceProfile からベースパラメータを取得
    if voice_profile and preset:
        profile_params = voice_profile.compute_params(
            preset=preset,
            emotion=emotion,
            intensity=intensity,
        )
        kwargs.update(profile_params)

    # 明示的な引数で上書き（デフォルト値以外の場合）
    kwargs["speed"] = speed
    if voice_id:
        kwargs["voice_id"] = voice_id
    if pitch != 0.0:
        kwargs["pitch"] = pitch
    if volume != 0.0:
        kwargs["volume"] = volume
    if intonation != 1.0:
        kwargs["intonation"] = intonation

    # TTSParams で上書き
    if params:
        if params.voice_id:
            kwargs["voice_id"] = params.voice_id
        if params.speed != 1.0:
            kwargs["speed"] = params.speed
        if params.pitch != 0.0:
            kwargs["pitch"] = params.pitch
        if params.volume != 0.0:
            kwargs["volume"] = params.volume
        if params.intonation != 1.0:
            kwargs["intonation"] = params.intonation
        if params.huskiness != 0.0:
            kwargs["huskiness"] = params.huskiness
        if params.alp != 0.0:
            kwargs["alp"] = params.alp
        if params.style_weights:
            kwargs["style_weights"] = params.style_weights
        if params.extra:
            kwargs.update(params.extra)

    kwargs.update(extra)
    return kwargs


def _to_synthesis_result(result: AudioResult) -> SynthesisResult:
    """内部AudioResultを公開SynthesisResultに変換."""
    return SynthesisResult(
        audio_data=result.audio_data,
        format=result.format,
        sample_rate=result.sample_rate,
        duration=result.duration,
    )


def _voice_intent_from_kwargs(kwargs: dict[str, object]) -> VoiceIntent:
    """legacy SDK kwargsをprovider非依存のVoiceIntentへ変換する。"""

    emotion = kwargs.get("emotion", "neutral")
    intensity = kwargs.get("intensity", 0.5)
    try:
        intensity_value = float(intensity)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        intensity_value = 0.5
    # Provider-specific voice IDs stay in request metadata; they never become a VoiceIntent.
    values = {
        "speaking_rate": float(kwargs.get("speed", 1.0)),
        "pitch": float(kwargs.get("pitch", 0.0)),
        "volume": float(kwargs.get("volume", 0.0)),
        "intonation": float(kwargs.get("intonation", 1.0)),
        "emotion": str(emotion),
        "intensity": max(0.0, min(1.0, intensity_value)),
        "preferred_voice_profile": (
            str(kwargs["preset"]) if kwargs.get("preset") is not None else None
        ),
    }
    try:
        return VoiceIntent(**values)
    except (TypeError, ValueError):
        values["emotion"] = "neutral"
        return VoiceIntent(**values)


def _add_intent_kwargs(
    kwargs: dict[str, object], *, preset: str | None, emotion: str, intensity: float
) -> None:
    """明示指定された感情意図をlegacy kwargsへ補う。"""

    if preset is not None:
        kwargs.setdefault("preset", preset)
    if emotion != "neutral":
        kwargs["emotion"] = emotion
    if intensity != 0.5:
        kwargs["intensity"] = intensity


def _to_api_synthesis_result(result: object) -> SynthesisResult:
    """application SynthesisResultを既存APIのresult DTOへ変換する。"""

    audio_data = getattr(result, "audio_data", None)
    return SynthesisResult(
        audio_data=audio_data or b"",
        format=str(getattr(result, "format", "wav")),
        sample_rate=getattr(result, "sample_rate", None),
        duration=getattr(result, "duration", None),
    )
