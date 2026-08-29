"""Batch synthesis engine for studio scripts."""

from __future__ import annotations

import struct
import wave
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from ..application.synthesis import SynthesisRequest, SynthesisService
from ..bootstrap import create_synthesis_service
from ..core.models import VoiceIntent
from ..tts.base import TTSProvider
from .cache import SynthCache
from .models import ScriptLine, SpeakerMapping, SynthResult
from .naming import FileNamer


class StudioSynthesizer:
    """バッチ合成エンジン."""

    def __init__(
        self,
        providers: dict[str, TTSProvider] | None = None,
        default_provider: str = "voicevox",
        *,
        synthesis_service: SynthesisService | None = None,
        cache: object | None = None,
    ):
        self.providers = providers or {}
        self.default_provider = default_provider
        if synthesis_service is None:
            if not self.providers:
                raise ValueError("providers or synthesis_service is required")
            self._service = create_synthesis_service(
                self.providers,
                primary=default_provider,
                cache=cache,
            )
            self._owns_service = True
        else:
            self._service = synthesis_service
            self._owns_service = False

    @property
    def synthesis_service(self) -> SynthesisService:
        """Studioが利用する共有合成service。"""

        return self._service

    async def synthesize_line(
        self,
        line: ScriptLine,
        mapping: SpeakerMapping,
        output_path: Path,
    ) -> float:
        """1行を合成してWAVに書き出し、duration(秒)を返す."""
        params = self._build_params(line, mapping)
        voice = params.pop("voice_id", mapping.voice_id)
        voice_intent = _voice_intent_from_params(line, params)
        result = await self._service.synthesize(
            SynthesisRequest(
                text=line.text,
                voice_intent=voice_intent,
                # The configured primary provider may fall back; an explicitly different
                # speaker mapping remains pinned to its requested provider.
                provider=(
                    None
                    if mapping.provider == self._service.primary_provider.name
                    else mapping.provider
                ),
                destination=str(output_path),
                metadata={"voice_id": voice, **params},
            )
        )
        if result.audio_data and not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(result.audio_data)
        duration = result.duration or _get_wav_duration_from_file(output_path)
        if not output_path.exists():
            raise ValueError(f"SynthesisService returned no artifact for {line.index}")
        return duration

    async def synthesize_all(
        self,
        lines: list[ScriptLine],
        speaker_mappings: dict[str, SpeakerMapping],
        namer: FileNamer,
        output_dir: Path,
        cache: SynthCache | None = None,
        on_progress: Callable[[int, int, ScriptLine], None] | None = None,
    ) -> list[SynthResult]:
        """全行を合成."""
        audio_dir = output_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        results: list[SynthResult] = []

        for i, line in enumerate(lines):
            mapping = speaker_mappings.get(line.speaker)
            if not mapping:
                # デフォルトプロバイダーのデフォルトボイス
                mapping = SpeakerMapping(
                    speaker=line.speaker,
                    provider=self.default_provider,
                    voice_id="",
                )

            wav_name = namer.wav_name(line)
            wav_path = audio_dir / wav_name
            txt_name = namer.txt_name(line)
            txt_path = audio_dir / txt_name if txt_name else None

            # キャッシュチェック
            if cache and cache.is_cached(line, mapping, wav_path):
                duration = _get_wav_duration_from_file(wav_path)
                logger.debug(f"Cache hit: line={line.index + 1}, chars={len(line.text)}")
            else:
                logger.info(
                    f"[{i + 1}/{len(lines)}] {line.speaker}: {line.text[:40]}"
                )
                duration = await self.synthesize_line(line, mapping, wav_path)
                if cache:
                    cache.record(line, mapping, wav_path)

            results.append(SynthResult(
                line_index=line.index,
                wav_path=wav_path,
                txt_path=txt_path,
                duration=duration,
                speaker=line.speaker,
                text=line.text,
            ))

            if on_progress:
                on_progress(i + 1, len(lines), line)

        return results

    async def preview_line(
        self, line: ScriptLine, mapping: SpeakerMapping
    ) -> None:
        """1行をプレビュー再生."""
        from ..tts.playback import play_wav

        params = self._build_params(line, mapping)
        voice = params.pop("voice_id", mapping.voice_id)
        voice_intent = _voice_intent_from_params(line, params)
        result = await self._service.synthesize(
            SynthesisRequest(
                text=line.text,
                voice_intent=voice_intent,
                provider=(
                    None
                    if mapping.provider == self._service.primary_provider.name
                    else mapping.provider
                ),
                metadata={"voice_id": voice, **params},
            )
        )
        if result.audio_data:
            await play_wav(result.audio_data)

    async def close(self) -> None:
        """内部生成したserviceだけを閉じる。"""

        if self._owns_service:
            await self._service.close()

    def _build_params(self, line: ScriptLine, mapping: SpeakerMapping) -> dict:
        """行パラメータとマッピングベースパラメータをマージ."""
        params = dict(mapping.base_params)
        params["voice_id"] = mapping.voice_id
        if line.tts_params:
            params.update(line.tts_params)
        return params


def _get_wav_duration_from_file(path: Path) -> float:
    """WAVファイルからdurationを取得."""
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return frames / rate
    except Exception:
        # WAVヘッダーを直接読む（waveモジュールが対応しない形式の場合）
        try:
            data = path.read_bytes()
            if len(data) >= 44 and data[:4] == b"RIFF":
                sample_rate = struct.unpack("<I", data[24:28])[0]
                data_size = struct.unpack("<I", data[40:44])[0]
                bits_per_sample = struct.unpack("<H", data[34:36])[0]
                channels = struct.unpack("<H", data[22:24])[0]
                if sample_rate > 0 and bits_per_sample > 0 and channels > 0:
                    bytes_per_sample = bits_per_sample // 8
                    return data_size / (sample_rate * channels * bytes_per_sample)
        except Exception:
            pass
    return 0.0


def _voice_intent_from_params(line: ScriptLine, params: dict) -> VoiceIntent:
    """Studioのlegacy line paramsをprovider非依存のVoiceIntentへ変換する。"""

    kwargs = {
        "speaking_rate": float(params.pop("speed", 1.0)),
        "pitch": float(params.pop("pitch", 0.0)),
        "volume": float(params.pop("volume", 0.0)),
        "intonation": float(params.pop("intonation", 1.0)),
        "emotion": line.emotion,
    }
    try:
        return VoiceIntent(**kwargs)
    except (TypeError, ValueError):
        # Existing Studio scripts may use provider-specific emotion labels.  Preserve those
        # labels in request metadata while using the domain's neutral fallback for routing.
        kwargs["emotion"] = "neutral"
        return VoiceIntent(**kwargs)
