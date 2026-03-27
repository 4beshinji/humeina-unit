"""Hash-based synthesis cache for skipping unchanged lines."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ScriptLine, SpeakerMapping


class SynthCache:
    """合成キャッシュ（hash-based skip）."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._cache_file = cache_dir / "cache.json"
        self._hashes: dict[str, str] = {}
        self._load()

    def is_cached(
        self, line: ScriptLine, mapping: SpeakerMapping, wav_path: Path
    ) -> bool:
        """WAVが存在しハッシュが一致すればTrue."""
        if not wav_path.exists():
            return False
        key = self._cache_key(wav_path)
        expected = self._compute_hash(line, mapping)
        return self._hashes.get(key) == expected

    def record(
        self, line: ScriptLine, mapping: SpeakerMapping, wav_path: Path
    ) -> None:
        """合成結果をキャッシュに記録."""
        key = self._cache_key(wav_path)
        self._hashes[key] = self._compute_hash(line, mapping)
        self._save()

    def _compute_hash(self, line: ScriptLine, mapping: SpeakerMapping) -> str:
        """テキスト + 話者 + プロバイダー + パラメータからハッシュ生成."""
        parts = [
            line.text,
            line.speaker,
            mapping.provider,
            mapping.voice_id,
            json.dumps(mapping.base_params, sort_keys=True),
        ]
        if line.tts_params:
            parts.append(json.dumps(line.tts_params, sort_keys=True))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def _cache_key(self, wav_path: Path) -> str:
        return wav_path.name

    def _load(self) -> None:
        if self._cache_file.exists():
            try:
                self._hashes = json.loads(self._cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._hashes = {}

    def _save(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file.write_text(
            json.dumps(self._hashes, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
