"""Parameter mapper — character × scene → TTS parameters."""

from pathlib import Path

import yaml

from ..nlp.scene_analyzer import AnalyzedSegment
from ..tts.base import TTSParams
from .character_db import CharacterDB, CharacterProfile


class ParamMapper:
    """キャラプロファイル × シーン感情 → TTSパラメータを合成."""

    def __init__(self, scene_config: dict | None = None):
        self.scenes = {}
        self.emotion_styles = {}
        self.voicevox_emotion_styles: dict = {}
        self.voicepeak_emotion_styles: dict = {}
        if scene_config:
            self.scenes = scene_config.get("scenes", {})
            self.emotion_styles = scene_config.get("emotion_styles", {})
            self.voicevox_emotion_styles = scene_config.get("voicevox_emotion_styles", {})
            self.voicepeak_emotion_styles = scene_config.get("voicepeak_emotion_styles", {})

    @classmethod
    def from_config_file(cls, path: Path) -> "ParamMapper":
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return cls(data)
        return cls()

    def map(
        self,
        segment: AnalyzedSegment,
        character_db: CharacterDB | None = None,
    ) -> TTSParams:
        """分析済みセグメントからTTSパラメータを生成."""
        params = TTSParams()

        # 1. キャラDBからベースパラメータ取得
        if segment.speaker and character_db:
            char = character_db.characters.get(segment.speaker)
            if char:
                self._apply_character(params, char)

        # 2. シーン修飾子を適用
        scene_mods = self.scenes.get(segment.scene, {})
        if scene_mods:
            params.speed *= scene_mods.get("speed", 1.0)
            params.volume += scene_mods.get("volume", 0.0)
            if "intonation" in scene_mods:
                params.intonation = scene_mods["intonation"]

        # 3. 感情からスタイルウェイトを設定
        style = self.emotion_styles.get(segment.emotion)
        if style:
            # 強度で中間補間
            intensity = segment.intensity
            if intensity < 1.0:
                neutral = self.emotion_styles.get("neutral", [1.0, 0.0, 0.0, 0.0, 0.0])
                style = [
                    n * (1 - intensity) + s * intensity for n, s in zip(neutral, style)
                ]
            params.style_weights = style

        return params

    def map_narration(
        self,
        scene: str,
        emotion: str,
        intensity: float,
        viewpoint_char: str | None = None,
        character_db: CharacterDB | None = None,
    ) -> TTSParams:
        """地の文用のTTSパラメータを生成."""
        params = TTSParams()

        # 視点キャラクターがいれば控えめにパラメータ適用
        if viewpoint_char and character_db:
            char = character_db.characters.get(viewpoint_char)
            if char and char.base_params:
                bp = char.base_params
                for k in ("pitch", "huskiness", "alp"):
                    if k in bp:
                        setattr(params, k, bp[k] * 0.3)

        # シーン修飾子
        scene_mods = self.scenes.get(scene, {})
        if scene_mods:
            params.speed *= scene_mods.get("speed", 1.0)
            params.volume += scene_mods.get("volume", 0.0)
            if "intonation" in scene_mods:
                params.intonation = scene_mods["intonation"]

        # 感情スタイルウェイト
        style = self.emotion_styles.get(emotion)
        if style:
            if intensity < 1.0:
                neutral = self.emotion_styles.get("neutral", [1.0, 0.0, 0.0, 0.0, 0.0])
                style = [
                    n * (1 - intensity) + s * intensity for n, s in zip(neutral, style)
                ]
            params.style_weights = style

        return params

    def apply_scene_mods(
        self, params: dict, scene: str, speed_default: float = 1.0
    ) -> None:
        """dict 形式のパラメータにシーン修飾子を適用.

        batch synthesizer 等、TTSParams ではなく dict を使う箇所向け.
        """
        scene_mods = self.scenes.get(scene, {})
        if scene_mods:
            params["speed"] = params.get("speed", speed_default) * scene_mods.get(
                "speed", 1.0
            )
            params["volume"] = params.get("volume", 0.0) + scene_mods.get(
                "volume", 0.0
            )

    def apply_emotion_style(
        self, params: dict, emotion: str, intensity: float
    ) -> None:
        """dict 形式のパラメータに感情スタイルウェイトを適用."""
        if "style_weights" in params:
            return
        emotion_style = self.emotion_styles.get(emotion)
        if emotion_style:
            if intensity < 1.0:
                neutral = self.emotion_styles.get(
                    "neutral", [1.0, 0.0, 0.0, 0.0, 0.0]
                )
                emotion_style = [
                    n * (1 - intensity) + s * intensity
                    for n, s in zip(neutral, emotion_style)
                ]
            params["style_weights"] = emotion_style

    def _apply_character(self, params: TTSParams, char: CharacterProfile) -> None:
        bp = char.base_params
        if not bp:
            return
        if char.voice_id:
            params.voice_id = char.voice_id
        if "speed" in bp:
            params.speed = bp["speed"]
        if "pitch" in bp:
            params.pitch = bp["pitch"]
        if "volume" in bp:
            params.volume = bp["volume"]
        if "intonation" in bp:
            params.intonation = bp["intonation"]
        if "huskiness" in bp:
            params.huskiness = bp["huskiness"]
        if "alp" in bp:
            params.alp = bp["alp"]
