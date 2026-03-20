"""AudioMixer — BGM ducking and SE insertion via ffmpeg filter graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from .asset_manager import AssetManager
from .config import VideoConfig
from .timeline import TimelineEvent


class AudioMixer:
    """TTS音声 + BGM + SE のミキシング."""

    def __init__(self, config: VideoConfig, asset_manager: AssetManager):
        self.config = config
        self.audio_cfg = config.audio
        self.asset_manager = asset_manager

    def mix_chapter_audio(
        self,
        tts_audio: Path,
        events: list[TimelineEvent],
        scene_segments: list[tuple[str, float, float]],
        output: Path,
    ) -> Path:
        """TTS音声 + BGMダッキング + SE → 合成音声ファイル.

        BGM/SEアセットが無ければ元のTTS音声をそのまま返す。
        """
        has_bgm = self.audio_cfg.bgm_enabled and self._has_any_bgm(
            scene_segments
        )
        has_se = self.audio_cfg.se_enabled
        scene_breaks = self._find_scene_breaks(events) if has_se else []
        se_path = (
            self.asset_manager.resolve_se("scene_break")
            if scene_breaks
            else None
        )

        if not has_bgm and not se_path:
            return tts_audio

        total_duration = events[-1].end_time if events else 0.0

        # 音声区間（ダッキング用）
        speech_ranges = [
            (e.start_time, e.end_time)
            for e in events
            if e.segment_type != "scene_break"
            and e.text.strip()
        ]

        cmd = ["ffmpeg", "-y", "-i", str(tts_audio)]
        inputs = 1  # index 0 = TTS

        filter_parts: list[str] = []
        mix_inputs: list[str] = ["[0:a]"]

        # BGMトラック
        if has_bgm:
            bgm_label = self._build_bgm_filter(
                cmd, scene_segments, speech_ranges,
                total_duration, inputs, filter_parts,
            )
            if bgm_label:
                mix_inputs.append(bgm_label)
                inputs += len([
                    s for s in scene_segments
                    if self.asset_manager.resolve_bgm(s[0])
                ])

        # SE
        if se_path and scene_breaks:
            se_label = self._build_se_filter(
                cmd, se_path, scene_breaks, inputs, filter_parts,
            )
            if se_label:
                mix_inputs.append(se_label)

        if len(mix_inputs) <= 1:
            return tts_audio

        # amix
        n = len(mix_inputs)
        mix_in = "".join(mix_inputs)
        filter_parts.append(
            f"{mix_in}amix=inputs={n}:duration=first"
            f":dropout_transition=0[aout]"
        )

        cmd += [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[aout]",
            "-codec:a", "pcm_s16le",
            str(output),
        ]

        logger.debug(f"AudioMixer: {' '.join(cmd[:10])}...")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            logger.error(f"Audio mix failed: {result.stderr[-300:]}")
            return tts_audio

        logger.info(f"Mixed audio: {output}")
        return output

    def _has_any_bgm(
        self, scene_segments: list[tuple[str, float, float]]
    ) -> bool:
        return any(
            self.asset_manager.resolve_bgm(scene)
            for scene, _, _ in scene_segments
        )

    def _find_scene_breaks(
        self, events: list[TimelineEvent]
    ) -> list[float]:
        return [
            e.start_time
            for e in events
            if e.segment_type == "scene_break"
        ]

    def _build_bgm_filter(
        self,
        cmd: list[str],
        scene_segments: list[tuple[str, float, float]],
        speech_ranges: list[tuple[float, float]],
        total_duration: float,
        input_offset: int,
        filter_parts: list[str],
    ) -> str | None:
        """シーン別BGMフィルターを構築. 合成ラベルを返す."""
        bgm_idx = input_offset
        seg_labels: list[str] = []

        vol = self.audio_cfg.bgm_volume
        idle_vol = self.audio_cfg.bgm_idle_volume
        fade = self.audio_cfg.ducking_fade

        for scene, start, end in scene_segments:
            bgm_path = self.asset_manager.resolve_bgm(scene)
            if not bgm_path:
                continue

            seg_dur = end - start
            cmd += ["-i", bgm_path]

            label = f"[bgm{bgm_idx}]"
            # ループ + トリム + ダッキング
            # 音声区間ではvol、それ以外はidle_vol
            volume_expr = self._ducking_expr(
                speech_ranges, start, end, vol, idle_vol, fade
            )
            filter_parts.append(
                f"[{bgm_idx}:a]"
                f"aloop=loop=-1:size=2e+09,"
                f"atrim=0:{seg_dur:.3f},"
                f"adelay={int(start * 1000)}|{int(start * 1000)},"
                f"volume='{volume_expr}':eval=frame"
                f"{label}"
            )
            seg_labels.append(label)
            bgm_idx += 1

        if not seg_labels:
            return None

        if len(seg_labels) == 1:
            return seg_labels[0]

        # 複数BGMをamix
        joined = "".join(seg_labels)
        out_label = "[bgm_all]"
        filter_parts.append(
            f"{joined}amix=inputs={len(seg_labels)}"
            f":duration=longest{out_label}"
        )
        return out_label

    def _ducking_expr(
        self,
        speech_ranges: list[tuple[float, float]],
        seg_start: float,
        seg_end: float,
        vol: float,
        idle_vol: float,
        fade: float,
    ) -> str:
        """ffmpeg volume expression: 音声区間でvol、それ以外でidle_vol."""
        # セグメント範囲内の音声区間のみ
        relevant = [
            (max(s, seg_start), min(e, seg_end))
            for s, e in speech_ranges
            if s < seg_end and e > seg_start
        ]
        if not relevant:
            return str(idle_vol)

        # シンプルなアプローチ: 固定音量（複雑な式はffmpegで問題を起こしがち）
        return str(vol)

    def _build_se_filter(
        self,
        cmd: list[str],
        se_path: str,
        break_times: list[float],
        input_offset: int,
        filter_parts: list[str],
    ) -> str | None:
        """SEフィルターを構築."""
        if not break_times:
            return None

        cmd += ["-i", se_path]
        se_idx = input_offset
        vol = self.audio_cfg.se_volume

        # 最初のscene_breakにSEを配置
        delay_ms = int(break_times[0] * 1000)
        label = "[se_out]"
        filter_parts.append(
            f"[{se_idx}:a]"
            f"volume={vol},"
            f"adelay={delay_ms}|{delay_ms}"
            f"{label}"
        )

        # 複数breakがある場合は追加SEを重ねる
        if len(break_times) > 1:
            se_labels = [label]
            for i, t in enumerate(break_times[1:], 1):
                cmd += ["-i", se_path]
                extra_idx = se_idx + i
                extra_label = f"[se{i}]"
                d = int(t * 1000)
                filter_parts.append(
                    f"[{extra_idx}:a]"
                    f"volume={vol},"
                    f"adelay={d}|{d}"
                    f"{extra_label}"
                )
                se_labels.append(extra_label)

            joined = "".join(se_labels)
            label = "[se_mixed]"
            filter_parts.append(
                f"{joined}amix=inputs={len(se_labels)}"
                f":duration=longest{label}"
            )

        return label
