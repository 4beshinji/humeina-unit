"""Batch synthesis engine for studio scripts."""

from __future__ import annotations

import struct
import wave
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from ..tts.base import TTSProvider
from .cache import SynthCache
from .models import ScriptLine, SpeakerMapping, SynthResult
from .naming import FileNamer


class StudioSynthesizer:
    """バッチ合成エンジン."""

    def __init__(
        self,
        providers: dict[str, TTSProvider],
        default_provider: str = "voicevox",
    ):
        self.providers = providers
        self.default_provider = default_provider

    async def synthesize_line(
        self,
        line: ScriptLine,
        mapping: SpeakerMapping,
        output_path: Path,
    ) -> float:
        """1行を合成してWAVに書き出し、duration(秒)を返す."""
        provider = self.providers.get(mapping.provider)
        if not provider:
            raise ValueError(f"Provider not found: {mapping.provider}")

        params = self._build_params(line, mapping)
        voice = params.pop("voice_id", mapping.voice_id)
        speed = params.pop("speed", 1.0)

        result = await provider.synthesize(
            text=line.text, voice=voice, speed=speed, **params
        )

        # audio_dataが空の場合（VoiSonaリモート再生等）、synthesize_to_fileにフォールバック
        if not result.audio_data and hasattr(provider, "synthesize_to_file"):
            await provider.synthesize_to_file(
                text=line.text,
                output_path=output_path,
                voice=voice,
                speed=speed,
                **params,
            )
            duration = result.duration or _get_wav_duration_from_file(output_path)
            return duration

        # WAV書き出し
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(result.audio_data)

        return result.duration or _get_wav_duration_from_file(output_path)

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
                logger.debug(f"Cache hit: [{line.index + 1}] {line.text[:30]}")
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

        provider = self.providers.get(mapping.provider)
        if not provider:
            raise ValueError(f"Provider not found: {mapping.provider}")

        params = self._build_params(line, mapping)
        voice = params.pop("voice_id", mapping.voice_id)
        speed = params.pop("speed", 1.0)

        result = await provider.synthesize(
            text=line.text, voice=voice, speed=speed, **params
        )
        if result.audio_data:
            await play_wav(result.audio_data)

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
