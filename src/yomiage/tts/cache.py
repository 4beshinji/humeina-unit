"""TTS 合成結果の簡易ファイルキャッシュ."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TTSCache:
    """テキスト+パラメータでキー化されたファイルキャッシュ."""

    cache_dir: Path
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, engine: str, text: str, **params: object) -> str:
        """キャッシュキーを生成."""
        payload = json.dumps(
            {"engine": engine, "text": text, "params": params},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path(self, key: str, ext: str = "wav") -> Path:
        return self.cache_dir / f"{key}.{ext}"

    def get(self, engine: str, text: str, **params: object) -> bytes | None:
        """キャッシュから音声データを取得."""
        if not self.enabled:
            return None
        key = self._key(engine, text, **params)
        path = self._path(key)
        if path.exists():
            return path.read_bytes()
        return None

    def put(
        self,
        audio_data: bytes,
        engine: str,
        text: str,
        **params: object,
    ) -> None:
        """音声データをキャッシュに保存."""
        if not self.enabled or not audio_data:
            return
        key = self._key(engine, text, **params)
        path = self._path(key)
        path.write_bytes(audio_data)
