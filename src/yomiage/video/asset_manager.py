"""Asset resolution for video generation — backgrounds, images."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from .config import VideoConfig


class AssetManager:
    """シーン別背景画像を解決.

    assets/backgrounds/{scene_type}.jpg を探し、
    見つからなければシーン別単色背景のffmpegフィルターを返す。
    """

    def __init__(self, config: VideoConfig, base_dir: Path):
        self.config = config
        self.assets_dir = base_dir / config.assets_dir
        self.bg_dir = self.assets_dir / "backgrounds"

    def resolve_background(self, scene: str) -> str | None:
        """シーンに対応する背景画像パスを返す. なければNone."""
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            path = self.bg_dir / f"{scene}{ext}"
            if path.exists():
                logger.debug(f"Background found: {path}")
                return str(path.resolve())
        return None

    def get_scene_color(self, scene: str) -> str:
        """シーンに対応するデフォルト背景色を返す."""
        return self.config.background.scene_colors.get(scene, "#2C3E50")

    def get_background_input(self, scene: str, duration: float) -> list[str]:
        """ffmpeg入力引数を返す（画像またはcolor filter）."""
        bg_path = self.resolve_background(scene)
        width, height = self.config.resolution

        if bg_path:
            # 画像ファイルをloop入力として使う
            return [
                "-loop", "1",
                "-i", bg_path,
                "-t", f"{duration:.3f}",
            ]
        else:
            # 単色背景をcolor filterで生成
            color = self.get_scene_color(scene)
            return [
                "-f", "lavfi",
                "-i", f"color=c={color}:s={width}x{height}:d={duration:.3f}:r={self.config.fps}",
            ]
