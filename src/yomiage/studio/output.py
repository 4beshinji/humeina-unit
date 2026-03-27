"""Output writer — YMM4 txt, metadata JSON, SRT/ASS subtitles."""

from __future__ import annotations

import json
from pathlib import Path

from ..video.subtitle import _format_ass_time, _format_srt_time
from .models import ScriptLine, StudioProject, SynthResult


class StudioOutputWriter:
    """Studio出力ファイルライター."""

    def write_ymm4_txt(self, line: ScriptLine, txt_path: Path) -> None:
        """YMM4用テキストファイルを書き出し."""
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(line.original_text, encoding="utf-8")

    def write_metadata_json(self, project: StudioProject, output_path: Path) -> None:
        """メタデータJSONを書き出し."""
        speakers = sorted({r.speaker for r in project.results})
        total_duration = sum(r.duration for r in project.results)

        lines_data = []
        for r in project.results:
            entry: dict = {
                "index": r.line_index,
                "speaker": r.speaker,
                "text": r.text,
                "wav_file": r.wav_path.name,
                "duration": round(r.duration, 3),
            }
            # パラメータ情報追加
            mapping = project.speaker_mappings.get(r.speaker)
            if mapping:
                entry["params"] = {
                    "provider": mapping.provider,
                    "voice_id": mapping.voice_id,
                    **mapping.base_params,
                }
            lines_data.append(entry)

        metadata = {
            "project": project.name,
            "total_duration": round(total_duration, 3),
            "line_count": len(project.results),
            "speakers": speakers,
            "lines": lines_data,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_srt(
        self,
        results: list[SynthResult],
        output_path: Path,
        default_pause: float = 0.3,
    ) -> None:
        """SRT字幕ファイルを書き出し."""
        lines: list[str] = []
        current_time = 0.0

        for seq, r in enumerate(results, 1):
            start = _format_srt_time(current_time)
            end = _format_srt_time(current_time + r.duration)

            lines.append(str(seq))
            lines.append(f"{start} --> {end}")
            lines.append(f"[{r.speaker}] {r.text}")
            lines.append("")

            current_time += r.duration + default_pause

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")

    def write_ass(
        self,
        results: list[SynthResult],
        output_path: Path,
        speaker_colors: dict[str, str] | None = None,
        default_pause: float = 0.3,
    ) -> None:
        """ASS字幕ファイルを書き出し."""
        from ..video.subtitle import _hex_to_ass_color

        colors = speaker_colors or {}

        # ヘッダー
        lines: list[str] = [
            "[Script Info]",
            "Title: Studio Output",
            "ScriptType: v4.00+",
            "PlayResX: 1920",
            "PlayResY: 1080",
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,Noto Sans JP,48,"
            "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,3,1,"
            "2,20,20,60,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, "
            "MarginL, MarginR, MarginV, Effect, Text",
        ]

        current_time = 0.0
        for r in results:
            start = _format_ass_time(current_time)
            end = _format_ass_time(current_time + r.duration)

            # 話者色オーバーライド
            color_tag = ""
            if r.speaker in colors:
                ass_color = _hex_to_ass_color(colors[r.speaker])
                color_tag = f"{{\\c{ass_color}}}"

            lines.append(
                f"Dialogue: 0,{start},{end},Default,{r.speaker},"
                f"0,0,0,,{color_tag}{r.text}"
            )

            current_time += r.duration + default_pause

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8-sig")
