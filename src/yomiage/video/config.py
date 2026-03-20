"""Pydantic configuration models for video generation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubtitleConfig(BaseModel):
    """字幕設定."""

    font_size: int = 48
    font_name: str = "Noto Sans JP"
    outline_size: int = 3
    margin_bottom: int = 60
    max_chars_per_line: int = 20
    speaker_colors: dict[str, str] = Field(default_factory=lambda: {
        "_narrator": "#FFFFFF",
        "_dialogue": "#FFFF00",
        "_thought": "#87CEEB",
    })


class BackgroundConfig(BaseModel):
    """背景設定."""

    transition: str = "fade"
    transition_duration: float = 1.0
    scene_colors: dict[str, str] = Field(default_factory=lambda: {
        "daily": "#2C3E50",
        "battle": "#8B0000",
        "romance": "#FF69B4",
        "tense": "#1C1C1C",
        "comedy": "#FFD700",
        "sad": "#4A4A8A",
        "horror": "#0D0D0D",
    })


class VideoConfig(BaseModel):
    """動画生成設定."""

    enabled: bool = False
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 24
    codec: str = "libx264"
    crf: int = 23
    preset: str = "medium"
    subtitle: SubtitleConfig = Field(default_factory=SubtitleConfig)
    background: BackgroundConfig = Field(default_factory=BackgroundConfig)
    assets_dir: str = "assets"

    @classmethod
    def from_dict(cls, data: dict) -> VideoConfig:
        """configのvideoセクションからVideoConfigを生成."""
        if not data:
            return cls()
        return cls(**data)
