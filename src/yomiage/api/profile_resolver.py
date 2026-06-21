"""VoiceProfile の自動解決ヘルパー."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tools.voice_profile import VoiceProfile


def resolve_voice_profile(
    voice_name: str,
    search_dirs: list[Path] | None = None,
) -> "VoiceProfile | None":
    """ボイス名から VoiceProfile を解決.

    Args:
        voice_name: ボイス名（例: nurse-robot-type-t_ja_JP）
        search_dirs: 検索ディレクトリ（None 時は config/voice_profiles）

    Returns:
        VoiceProfile または None
    """
    from ..tools.voice_profile import VoiceProfile

    if search_dirs is None:
        search_dirs = [Path("config/voice_profiles")]
    return VoiceProfile.find(voice_name, search_dirs=search_dirs)
