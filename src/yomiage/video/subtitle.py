"""ASS/SRT subtitle generation from timeline events."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from .config import VideoConfig
from .timeline import TimelineEvent

# 自動割当用の話者カラーパレット（鮮やかで区別しやすい色）
_SPEAKER_PALETTE = [
    "#FF6B6B",  # 赤
    "#4ECDC4",  # ティール
    "#FFE66D",  # 黄
    "#A8E6CF",  # ミント
    "#FF8B94",  # サーモン
    "#DDA0DD",  # プラム
    "#98D8C8",  # セージ
    "#F7DC6F",  # ゴールド
    "#BB8FCE",  # ラベンダー
    "#85C1E9",  # スカイ
]


def _format_ass_time(seconds: float) -> str:
    """秒数をASS形式の時間文字列に変換 (H:MM:SS.cc)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int((s - int(s)) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def _format_srt_time(seconds: float) -> str:
    """秒数をSRT形式の時間文字列に変換 (HH:MM:SS,mmm)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    ms = int((s - int(s)) * 1000)
    return f"{h:02d}:{m:02d}:{int(s):02d},{ms:03d}"


def _hex_to_ass_color(hex_color: str) -> str:
    """#RRGGBB → ASS形式 &H00BBGGRR (BGR逆順、透明度00)."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _wrap_text(text: str, max_chars: int) -> str:
    """日本語テキストを句読点優先で折り返す."""
    if len(text) <= max_chars:
        return text

    lines: list[str] = []
    remaining = text

    while len(remaining) > max_chars:
        # 句読点・記号で区切れる位置を探す
        best_break = max_chars
        for i in range(min(max_chars, len(remaining)) - 1, max_chars // 2, -1):
            if remaining[i] in "、。！？」）】》…―":
                best_break = i + 1
                break

        lines.append(remaining[:best_break])
        remaining = remaining[best_break:]

    if remaining:
        lines.append(remaining)

    return "\\N".join(lines)


class SubtitleGenerator:
    """ASS/SRT字幕ファイル生成."""

    def __init__(self, config: VideoConfig):
        self.config = config
        self.sub_config = config.subtitle
        self._speaker_color_map: dict[str, str] = {}
        self._palette_index = 0

    def _get_speaker_color(self, speaker: str | None, segment_type: str) -> str:
        """話者・セグメントタイプに応じた色を返す."""
        colors = self.sub_config.speaker_colors

        # セグメントタイプ別デフォルト
        if segment_type == "narration" or speaker is None:
            return colors.get("_narrator", "#FFFFFF")
        if segment_type == "thought":
            return colors.get("_thought", "#87CEEB")

        # configに明示的な話者色があればそれを使う
        if speaker in colors:
            return colors[speaker]

        # 自動割当
        if speaker not in self._speaker_color_map:
            # まずデフォルトのセリフ色が割当0番目
            if not self._speaker_color_map:
                default_dialogue = colors.get("_dialogue", "#FFFF00")
                self._speaker_color_map[speaker] = default_dialogue
            else:
                color = _SPEAKER_PALETTE[
                    self._palette_index % len(_SPEAKER_PALETTE)
                ]
                self._speaker_color_map[speaker] = color
                self._palette_index += 1

        return self._speaker_color_map[speaker]

    def generate_ass(
        self,
        events: list[TimelineEvent],
        output: Path,
        title: str = "",
        chapter_title: str = "",
    ) -> Path:
        """ASS字幕ファイルを生成."""
        width, height = self.config.resolution
        sc = self.sub_config
        chapter_title_size = int(sc.font_size * 1.5)

        # ASS header
        lines: list[str] = [
            "[Script Info]",
            f"Title: {title}",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
        ]

        # デフォルトスタイル（白・ナレーション用）
        lines.append(
            f"Style: Default,{sc.font_name},{sc.font_size},"
            f"&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            f"0,0,0,0,100,100,0,0,1,{sc.outline_size},1,"
            f"2,20,20,{sc.margin_bottom},1"
        )

        # セグメントタイプ別スタイル
        for style_name, hex_color in [
            ("Narration", sc.speaker_colors.get("_narrator", "#FFFFFF")),
            ("Dialogue", sc.speaker_colors.get("_dialogue", "#FFFF00")),
            ("Thought", sc.speaker_colors.get("_thought", "#87CEEB")),
        ]:
            ass_color = _hex_to_ass_color(hex_color)
            lines.append(
                f"Style: {style_name},{sc.font_name},{sc.font_size},"
                f"{ass_color},&H000000FF,&H00000000,&H80000000,"
                f"0,{'1' if style_name == 'Thought' else '0'},0,0,"
                f"100,100,0,0,1,{sc.outline_size},1,"
                f"2,20,20,{sc.margin_bottom},1"
            )

        # チャプタータイトルスタイル（中央配置、大きめフォント、フェードアウト）
        lines.append(
            f"Style: ChapterTitle,{sc.font_name},{chapter_title_size},"
            f"&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            f"1,0,0,0,100,100,0,0,1,{sc.outline_size + 1},2,"
            f"5,20,20,20,1"  # Alignment=5 (center-center)
        )

        lines.extend(["", "[Events]"])
        lines.append(
            "Format: Layer, Start, End, Style, Name, "
            "MarginL, MarginR, MarginV, Effect, Text"
        )

        # チャプタータイトルオーバーレイ（冒頭3秒、フェードアウト）
        if chapter_title:
            ct_end = min(3.0, events[-1].end_time if events else 3.0)
            lines.append(
                f"Dialogue: 1,"
                f"{_format_ass_time(0.0)},{_format_ass_time(ct_end)},"
                f"ChapterTitle,,0,0,0,,"
                f"{{\\fad(0,1000)}}{chapter_title}"
            )

        # イベント生成
        for event in events:
            if event.segment_type == "scene_break":
                continue

            start = _format_ass_time(event.start_time)
            end = _format_ass_time(event.end_time)

            # スタイル選択
            if event.segment_type == "thought":
                style = "Thought"
            elif event.segment_type == "dialogue":
                style = "Dialogue"
            else:
                style = "Narration"

            # 話者別色オーバーライド（Dialogue時）
            speaker_color = self._get_speaker_color(
                event.speaker, event.segment_type
            )
            color_tag = ""
            if event.segment_type == "dialogue" and event.speaker:
                ass_color = _hex_to_ass_color(speaker_color)
                color_tag = f"{{\\c{ass_color}}}"

            text = _wrap_text(event.text, sc.max_chars_per_line)
            name = event.speaker or ""

            lines.append(
                f"Dialogue: 0,{start},{end},{style},{name},"
                f"0,0,0,,{color_tag}{text}"
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8-sig")
        logger.info(f"ASS subtitle: {output}")
        return output

    def generate_srt(
        self,
        events: list[TimelineEvent],
        output: Path,
    ) -> Path:
        """SRT字幕ファイルを生成."""
        lines: list[str] = []
        seq = 1

        for event in events:
            if event.segment_type == "scene_break":
                continue

            start = _format_srt_time(event.start_time)
            end = _format_srt_time(event.end_time)

            text = event.text
            # SRTでは \N ではなく改行
            if len(text) > self.sub_config.max_chars_per_line:
                text = _wrap_text(text, self.sub_config.max_chars_per_line)
                text = text.replace("\\N", "\n")

            # 話者プレフィックス
            prefix = ""
            if event.segment_type == "dialogue" and event.speaker:
                prefix = f"[{event.speaker}] "

            lines.append(str(seq))
            lines.append(f"{start} --> {end}")
            lines.append(f"{prefix}{text}")
            lines.append("")
            seq += 1

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"SRT subtitle: {output}")
        return output
