"""TTS 合成結果の簡易ファイルキャッシュ."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..utils.atomic_io import atomic_write_wav
from .audio_utils import validate_audio_bytes

CACHE_SCHEMA_VERSION = 2


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

    def get_key(self, key: str, *, ext: str = "wav") -> bytes | None:
        """canonical schema keyから検証済みentryだけを返す。"""

        if not self.enabled:
            return None
        path = self._path(f"v{CACHE_SCHEMA_VERSION}-{key}", ext)
        if not path.is_file():
            return None
        data = path.read_bytes()
        try:
            validate_audio_bytes(data, ext)
        except ValueError:
            path.unlink(missing_ok=True)
            return None
        return data

    def put_key(self, key: str, audio_data: bytes, *, ext: str = "wav") -> None:
        """検証後、canonical schema entryへatomic publishする。"""

        if not self.enabled or not audio_data:
            return
        validate_audio_bytes(audio_data, ext)
        path = self._path(f"v{CACHE_SCHEMA_VERSION}-{key}", ext)
        if ext == "wav":
            atomic_write_wav(path, audio_data)
            return
        raise ValueError(f"unsupported cache audio format: {ext}")

    def get(self, engine: str, text: str, **params: object) -> bytes | None:
        """キャッシュから音声データを取得."""
        if not self.enabled:
            return None
        key = self._key(engine, text, **params)
        path = self._path(key)
        if path.exists():
            data = path.read_bytes()
            try:
                validate_audio_bytes(data, path.suffix)
            except ValueError:
                path.unlink(missing_ok=True)
                return None
            return data
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
        validate_audio_bytes(audio_data, "wav")
        atomic_write_wav(path, audio_data)
