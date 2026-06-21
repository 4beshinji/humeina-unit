"""TTSBridge — unified interface to multiple TTS engines."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ..tts.base import AudioResult, TTSParams, TTSProvider
from ..tts.factory import create_provider
from ..tts.retry import RetryConfig, with_retry
from .models import SynthesisResult, VoiceInfo

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
        provider: TTSProvider,
        fallback: TTSProvider | None = None,
        retry_config: RetryConfig | None = None,
    ):
        self._provider = provider
        self._fallback = fallback
        self._retry_config = retry_config

    @classmethod
    def create(
        cls,
        engine: str,
        *,
        retry_config: RetryConfig | None = None,
        **kwargs: object,
    ) -> TTSBridge:
        """ファクトリ: エンジン名+パラメータで作成.

        Args:
            engine: "voicevox", "voisona", or "voicepeak"
            retry_config: リトライ設定
            **kwargs: url, username, password, default_voice etc.
        """
        from .config import TTSEngineConfig

        config = TTSEngineConfig(engine=engine, **kwargs)  # type: ignore[arg-type]
        provider = create_provider(config)
        return cls(provider, retry_config=retry_config)

    @classmethod
    def from_config(
        cls,
        config: TTSEngineConfig,
        fallback: TTSEngineConfig | None = None,
        *,
        retry_config: RetryConfig | None = None,
    ) -> TTSBridge:
        """TTSEngineConfigから作成."""
        provider = create_provider(config)
        fb = create_provider(fallback) if fallback else None
        return cls(provider, fb, retry_config=retry_config)

    @classmethod
    def from_provider(
        cls,
        provider: TTSProvider,
        fallback: TTSProvider | None = None,
        *,
        retry_config: RetryConfig | None = None,
    ) -> TTSBridge:
        """既存のTTSProviderインスタンスから作成."""
        return cls(provider, fallback, retry_config=retry_config)

    @property
    def engine_name(self) -> str:
        """現在のプライマリエンジン名."""
        return self._provider.name

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        speed: float = 1.0,
        pitch: float = 0.0,
        volume: float = 0.0,
        intonation: float = 1.0,
        params: TTSParams | None = None,
        **kwargs: object,
    ) -> SynthesisResult:
        """テキストを音声合成.

        VoiSonaなど audio_data が空のプロバイダーは
        synthesize_to_file 経由で一時ファイルからバイト列を取得する。
        """
        synth_kwargs = _build_synth_kwargs(
            voice_id=voice_id,
            speed=speed,
            pitch=pitch,
            volume=volume,
            intonation=intonation,
            params=params,
            **kwargs,
        )

        result = await self._synthesize_with_fallback(text, synth_kwargs)

        # audio_data が空の場合（VoiSona等）はファイル経由で取得
        if not result.audio_data:
            result = await self._synthesize_via_file(text, synth_kwargs)

        return _to_synthesis_result(result)

    async def synthesize_to_file(
        self,
        text: str,
        path: str | Path,
        *,
        voice_id: str | None = None,
        speed: float = 1.0,
        pitch: float = 0.0,
        params: TTSParams | None = None,
        **kwargs: object,
    ) -> SynthesisResult:
        """テキストを音声合成しファイルに保存."""
        synth_kwargs = _build_synth_kwargs(
            voice_id=voice_id, speed=speed, pitch=pitch, params=params, **kwargs
        )
        provider = self._provider
        if hasattr(provider, "synthesize_to_file"):
            result = await provider.synthesize_to_file(
                text, str(path), **synth_kwargs
            )
        else:
            result = await provider.synthesize(text, **synth_kwargs)
            Path(path).write_bytes(result.audio_data)

        # ファイルから読み取ってaudio_dataを埋める
        audio_data = result.audio_data
        if not audio_data:
            file_path = Path(path)
            if file_path.exists():
                audio_data = file_path.read_bytes()

        return SynthesisResult(
            audio_data=audio_data,
            format=result.format,
            sample_rate=result.sample_rate,
            duration=result.duration,
        )

    async def list_voices(self) -> list[VoiceInfo]:
        """利用可能なボイス一覧を返す."""
        raw_voices = await self._provider.list_voices()
        return [
            VoiceInfo(
                id=str(v.get("id", "")),
                name=v.get("label", v.get("name", "")),
                engine=self._provider.name,
                gender=v.get("gender"),
                age_group=v.get("age_group"),
                extra={
                    k: v2
                    for k, v2 in v.items()
                    if k not in ("id", "label", "name", "gender", "age_group")
                },
            )
            for v in raw_voices
        ]

    async def is_available(self) -> bool:
        """プロバイダーが利用可能か確認."""
        return await self._provider.is_available()

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

    # --- internal ---

    async def _synthesize_with_fallback(
        self, text: str, synth_kwargs: dict
    ) -> AudioResult:
        """primary で合成を試み、失敗時に fallback へ.

        リトライ設定があれば primary/fallback それぞれでリトライする。
        """
        async def _try_primary() -> AudioResult:
            return await self._provider.synthesize(text, **synth_kwargs)

        try:
            if self._retry_config:
                return await with_retry(
                    _try_primary,
                    self._retry_config,
                    operation_name=f"TTS ({self._provider.name})",
                )
            return await _try_primary()
        except Exception as e:
            if self._fallback:
                logger.warning(
                    f"Primary TTS ({self._provider.name}) failed: {e}, "
                    f"trying fallback ({self._fallback.name})"
                )
                async def _try_fallback() -> AudioResult:
                    return await self._fallback.synthesize(text, **synth_kwargs)

                if self._retry_config:
                    return await with_retry(
                        _try_fallback,
                        self._retry_config,
                        operation_name=f"TTS fallback ({self._fallback.name})",
                    )
                return await _try_fallback()
            raise

    async def _synthesize_via_file(
        self, text: str, synth_kwargs: dict
    ) -> AudioResult:
        """一時ファイル経由で音声データを取得（VoiSona等向け）."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = Path(f.name)

        try:
            provider = self._provider
            if hasattr(provider, "synthesize_to_file"):
                result = await provider.synthesize_to_file(
                    text, str(tmp_path), **synth_kwargs
                )
                audio_data = tmp_path.read_bytes() if tmp_path.exists() else b""
                return AudioResult(
                    audio_data=audio_data,
                    format=result.format,
                    sample_rate=result.sample_rate,
                    duration=result.duration,
                )
            # synthesize_to_file がない場合はそのまま返す
            return await provider.synthesize(text, **synth_kwargs)
        finally:
            tmp_path.unlink(missing_ok=True)


def _build_synth_kwargs(
    *,
    voice_id: str | None = None,
    speed: float = 1.0,
    pitch: float = 0.0,
    volume: float = 0.0,
    intonation: float = 1.0,
    params: TTSParams | None = None,
    **extra: object,
) -> dict:
    """synthesize()呼び出し用のkwargsを構築."""
    kwargs: dict = {"speed": speed}
    if voice_id:
        kwargs["voice_id"] = voice_id
    if pitch != 0.0:
        kwargs["pitch"] = pitch
    if volume != 0.0:
        kwargs["volume"] = volume
    if intonation != 1.0:
        kwargs["intonation"] = intonation

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
