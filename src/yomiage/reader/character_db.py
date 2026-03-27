"""Character database — per-work character tracking and voice assignment."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from loguru import logger

DEFAULT_CHAR_DIR = Path("data/characters")


@dataclass
class CharacterProfile:
    """キャラクタープロファイル."""

    name: str
    gender: str | None = None
    age_group: str | None = None
    personality: str | None = None
    voice_id: str | None = None
    voice_locked: bool = False
    base_params: dict = field(default_factory=dict)


class CharacterDB:
    """作品ごとのキャラクターデータベース（JSONファイル永続化）."""

    def __init__(
        self, work_id: str, data_dir: Path | None = None, *, persist: bool = True
    ):
        self.work_id = work_id
        self._persist = persist
        self.data_dir = data_dir or DEFAULT_CHAR_DIR
        self._characters: dict[str, CharacterProfile] = {}
        if persist:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    def _path(self) -> Path:
        return self.data_dir / f"{self.work_id}.json"

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for name, char_data in data.items():
                self._characters[name] = CharacterProfile(**char_data)
        except Exception as e:
            logger.warning(f"Failed to load character DB: {e}")

    def _save(self) -> None:
        if not self._persist:
            return
        path = self._path()
        data = {name: asdict(char) for name, char in self._characters.items()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    @property
    def characters(self) -> dict[str, CharacterProfile]:
        return dict(self._characters)

    @property
    def known_names(self) -> list[str]:
        return list(self._characters.keys())

    def get_or_create(
        self, name: str, profile_hint: dict | None = None
    ) -> CharacterProfile:
        """キャラクターを取得。存在しなければ作成."""
        if name in self._characters:
            char = self._characters[name]
            # ヒントで未設定フィールドを埋める
            if profile_hint:
                if not char.gender and "gender" in profile_hint:
                    char.gender = profile_hint["gender"]
                if not char.age_group and "age_group" in profile_hint:
                    char.age_group = profile_hint["age_group"]
                if not char.personality and "personality" in profile_hint:
                    char.personality = profile_hint["personality"]
            return char

        hint = profile_hint or {}
        char = CharacterProfile(
            name=name,
            gender=hint.get("gender"),
            age_group=hint.get("age_group"),
            personality=hint.get("personality"),
        )
        self._characters[name] = char
        self._save()
        logger.info(f"New character registered: {name}")
        return char

    def assign_voice(
        self,
        name: str,
        available_voices: list[dict],
        favorites: list[dict] | None = None,
    ) -> str | None:
        """キャラクターにボイスを自動割当.

        Returns assigned voice_id, or None if no suitable voice found.
        """
        char = self._characters.get(name)
        if not char:
            return None
        if char.voice_locked and char.voice_id:
            return char.voice_id

        # 使用可能なボイスプール
        pool = favorites if favorites else available_voices
        if not pool:
            return None

        # 既に割当済みのボイスを除外
        used_voices = {
            c.voice_id for c in self._characters.values() if c.voice_id and c.name != name
        }

        # フィルタ: 性別・年齢層
        candidates = []
        for v in pool:
            vid = str(v.get("id", ""))
            if vid in used_voices:
                continue
            # 性別マッチ
            if char.gender and v.get("gender") and v["gender"] != char.gender:
                continue
            # 年齢層マッチ
            if char.age_group and v.get("age_group") and v["age_group"] != char.age_group:
                continue
            candidates.append(v)

        # マッチなければ全プールから重複除外のみ
        if not candidates:
            candidates = [v for v in pool if str(v.get("id", "")) not in used_voices]

        if not candidates:
            candidates = pool  # 最終フォールバック

        voice = candidates[0]
        char.voice_id = str(voice.get("id", ""))
        self._save()
        logger.info(f"Voice assigned: {name} → {char.voice_id}")
        return char.voice_id

    def bulk_create(self, characters: list[dict]) -> None:
        """キャラクターを一括登録."""
        for char_data in characters:
            name = char_data.get("name")
            if not name:
                continue
            self.get_or_create(name, profile_hint=char_data)
        self._save()
        logger.info(f"Bulk created {len(characters)} characters")

    def assign_all_voices(self, available_voices: list[dict]) -> dict[str, str]:
        """全キャラクターにボイスを自動割当.

        Returns: {キャラクター名: voice_id} のマッピング
        """
        assignments: dict[str, str] = {}
        for name in self._characters:
            voice_id = self.assign_voice(name, available_voices)
            if voice_id:
                assignments[name] = voice_id
        return assignments

    def lock_voice(self, name: str, voice_id: str) -> None:
        """ユーザによるボイス固定."""
        char = self.get_or_create(name)
        char.voice_id = voice_id
        char.voice_locked = True
        self._save()
        logger.info(f"Voice locked: {name} → {voice_id}")
