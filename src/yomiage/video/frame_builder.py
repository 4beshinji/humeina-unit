"""Frame builder — portrait overlay filter graphs and title card generation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from .asset_manager import AssetManager
from .config import VideoConfig
from .timeline import TimelineEvent


class PortraitOverlay:
    """立ち絵オーバーレイのffmpeg filter_complex を構築."""

    def __init__(self, config: VideoConfig, asset_manager: AssetManager):
        self.config = config
        self.portrait_cfg = config.portrait
        self.asset_manager = asset_manager

    def build_overlay_command(
        self,
        events: list[TimelineEvent],
        bg_video: Path,
        audio_path: Path,
        ass_path: Path,
        output: Path,
    ) -> list[str] | None:
        """立ち絵オーバーレイ付きffmpegコマンドを構築.

        立ち絵が無ければNoneを返す（フォールバック用）。
        """
        if not self.portrait_cfg.enabled:
            return None

        # ユニークな立ち絵を収集
        portrait_map = self._collect_unique_portraits(events)
        if not portrait_map:
            return None

        width, height = self.config.resolution
        max_h = int(height * self.portrait_cfg.max_height_ratio)
        margin_x = self.portrait_cfg.margin_x
        margin_y = self.portrait_cfg.margin_y

        # x座標
        if self.portrait_cfg.position == "bottom_left":
            x_expr = str(margin_x)
        else:  # bottom_right
            x_expr = f"W-w-{margin_x}"
        y_expr = f"H-h-{margin_y}"

        cmd = ["ffmpeg", "-y", "-i", str(bg_video)]

        # 立ち絵入力を追加
        portrait_paths = list(portrait_map.keys())
        path_to_idx: dict[str, int] = {}
        for i, ppath in enumerate(portrait_paths):
            cmd += ["-i", ppath]
            path_to_idx[ppath] = i + 1  # index 0 = bg_video

        cmd += ["-i", str(audio_path)]
        audio_idx = len(portrait_paths) + 1

        # filter_complex構築
        filters: list[str] = []
        current_label = "[0:v]"

        # 各立ち絵をスケーリング
        for ppath, idx in path_to_idx.items():
            filters.append(
                f"[{idx}:v]scale=-1:{max_h}"
                f":force_original_aspect_ratio=decrease"
                f"[p{idx}]"
            )

        # イベントごとにoverlayをチェーン
        overlay_count = 0
        for event in events:
            if event.segment_type == "scene_break":
                continue
            ppath = self.asset_manager.get_portrait_for_event(event)
            if not ppath or ppath not in path_to_idx:
                continue

            idx = path_to_idx[ppath]
            start = event.start_time
            end = event.end_time
            out_label = f"[v{overlay_count}]"

            # enable区間 + フェード
            enable = f"between(t,{start:.3f},{end:.3f})"
            filters.append(
                f"{current_label}[p{idx}]overlay="
                f"x={x_expr}:y={y_expr}:"
                f"enable='{enable}'"
                f"{out_label}"
            )
            current_label = out_label
            overlay_count += 1

        if overlay_count == 0:
            return None

        # ASS字幕を最後に焼き込み
        ass_escaped = (
            str(ass_path.resolve())
            .replace("\\", "\\\\")
            .replace(":", "\\:")
        )
        final_label = "[vfinal]"
        filters.append(
            f"{current_label}ass='{ass_escaped}'{final_label}"
        )

        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", final_label,
            "-map", f"{audio_idx}:a",
            "-c:v", self.config.codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output),
        ]
        return cmd

    def _collect_unique_portraits(
        self, events: list[TimelineEvent]
    ) -> dict[str, str]:
        """イベント列からユニーク立ち絵パスを収集."""
        return self.asset_manager.collect_portraits(events)


class TitleCardGenerator:
    """タイトルカード画像生成 (Pillow)."""

    def __init__(self, config: VideoConfig):
        self.config = config
        self.tc_cfg = config.title_card

    def generate(
        self,
        title: str,
        subtitle: str = "",
        output_dir: Path | None = None,
        bg_color: str = "#1E293B",
    ) -> Path | None:
        """タイトルカードPNG → ffmpegで短いMP4に変換."""
        if not self.tc_cfg.enabled:
            return None

        try:
            from PIL import Image, ImageDraw
        except ImportError:
            logger.warning("Pillow not installed — skipping title card")
            return None

        width, height = self.config.resolution
        img = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        # フォント取得
        title_font = self._get_font(self.tc_cfg.font_size)
        sub_font = self._get_font(self.tc_cfg.subtitle_font_size)

        # タイトル描画（中央）
        if title:
            bbox = draw.textbbox((0, 0), title, font=title_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = (width - tw) // 2
            ty = (height - th) // 2 - (30 if subtitle else 0)
            draw.text((tx, ty), title, fill="#FFFFFF", font=title_font)

        # サブタイトル描画
        if subtitle:
            bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
            sw = bbox[2] - bbox[0]
            sx = (width - sw) // 2
            sy = ty + th + 20 if title else height // 2
            draw.text(
                (sx, sy), subtitle, fill="#94A3B8", font=sub_font
            )

        # PNG保存
        if output_dir is None:
            output_dir = Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)
        png_path = output_dir / "_title_card.png"
        img.save(str(png_path))

        # ffmpegでMP4に変換
        mp4_path = output_dir / "_title_card.mp4"
        duration = self.tc_cfg.duration
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(png_path),
            "-f", "lavfi", "-i",
            f"anullsrc=r=24000:cl=mono:d={duration:.3f}",
            "-t", f"{duration:.3f}",
            "-c:v", self.config.codec,
            "-crf", str(self.config.crf),
            "-preset", self.config.preset,
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(mp4_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
        png_path.unlink(missing_ok=True)

        if result.returncode != 0:
            logger.error(f"Title card failed: {result.stderr[-200:]}")
            return None

        logger.info(f"Title card: {mp4_path}")
        return mp4_path

    def _get_font(self, size: int):
        """フォントを取得. 見つからなければデフォルト."""
        from PIL import ImageFont

        font_name = self.config.subtitle.font_name
        # 一般的なフォントパスを試行
        font_paths = [
            f"/usr/share/fonts/truetype/noto/{font_name.replace(' ', '')}-Regular.ttf",
            f"/usr/share/fonts/opentype/noto/{font_name.replace(' ', '')}-Regular.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for fp in font_paths:
            try:
                return ImageFont.truetype(fp, size)
            except (OSError, IOError):
                continue

        # フォント名で直接試行
        try:
            return ImageFont.truetype(font_name, size)
        except (OSError, IOError):
            pass

        return ImageFont.load_default(size=size)
