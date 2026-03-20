"""Asset resolution for video generation — backgrounds, portraits, audio."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from .config import VideoConfig
from .timeline import TimelineEvent


class AssetManager:
    """アセット解決: 背景・立ち絵・BGM・SE."""

    def __init__(self, config: VideoConfig, base_dir: Path):
        self.config = config
        self.assets_dir = base_dir / config.assets_dir
        self.bg_dir = self.assets_dir / "backgrounds"
        self.portrait_dir = self.assets_dir / "portraits"
        self.bgm_dir = self.assets_dir / "bgm"
        self.se_dir = self.assets_dir / "se"

    # --- 背景 ---

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
            return [
                "-loop", "1",
                "-i", bg_path,
                "-t", f"{duration:.3f}",
            ]
        else:
            color = self.get_scene_color(scene)
            return [
                "-f", "lavfi",
                "-i",
                f"color=c={color}:s={width}x{height}"
                f":d={duration:.3f}:r={self.config.fps}",
            ]

    # --- 立ち絵 (Phase 2) ---

    def resolve_portrait(
        self, character: str, emotion: str
    ) -> str | None:
        """キャラクターの立ち絵PNGパスを返す.

        探索順: {character}/{emotion}.png → neutral.png → default.png
        """
        if not character:
            return None

        char_dir = self.portrait_dir / character
        if not char_dir.is_dir():
            return None

        for name in (f"{emotion}.png", "neutral.png", "default.png"):
            path = char_dir / name
            if path.exists():
                logger.debug(f"Portrait found: {path}")
                return str(path.resolve())
        return None

    def get_portrait_for_event(self, event: TimelineEvent) -> str | None:
        """イベントの話者+感情から立ち絵パスを返す."""
        if not event.speaker or event.segment_type == "scene_break":
            return None
        return self.resolve_portrait(event.speaker, event.emotion)

    def collect_portraits(
        self, events: list[TimelineEvent]
    ) -> dict[str, str]:
        """イベント列からユニークな立ち絵パスを収集.

        Returns: {portrait_path: character_name}
        """
        portraits: dict[str, str] = {}
        for event in events:
            path = self.get_portrait_for_event(event)
            if path and path not in portraits:
                portraits[path] = event.speaker or ""
        return portraits

    # --- BGM (Phase 3) ---

    def resolve_bgm(self, scene: str) -> str | None:
        """シーンに対応するBGMファイルパスを返す."""
        for ext in (".mp3", ".wav", ".ogg", ".flac"):
            path = self.bgm_dir / f"{scene}{ext}"
            if path.exists():
                logger.debug(f"BGM found: {path}")
                return str(path.resolve())
        # フォールバック: default
        for ext in (".mp3", ".wav", ".ogg", ".flac"):
            path = self.bgm_dir / f"default{ext}"
            if path.exists():
                return str(path.resolve())
        return None

    def resolve_se(self, se_name: str) -> str | None:
        """SE名に対応するサウンドエフェクトパスを返す."""
        for ext in (".mp3", ".wav", ".ogg", ".flac"):
            path = self.se_dir / f"{se_name}{ext}"
            if path.exists():
                logger.debug(f"SE found: {path}")
                return str(path.resolve())
        return None
