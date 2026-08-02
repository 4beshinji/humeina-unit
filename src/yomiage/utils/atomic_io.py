"""ファイル成果物の原子的な公開を行うユーティリティ.

同一ディレクトリへ一時ファイルを作成してから ``Path.replace`` することで、
書き込み途中のJSON/WAVを読み手へ公開しない。プロセス間ロックはこの層では
行わず、同じ最終パスへの同時書き込みは「最後にreplaceできた内容が勝つ」という
last-writer-wins方針とする。各writerは固有の一時ファイルを使うため、途中の
writerが別writerの一時ファイルを削除することはない。
"""

from __future__ import annotations

import json
import os
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _fsync_directory(directory: Path) -> None:
    """置換後のディレクトリエントリを可能な環境で同期する."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            # Windows等、ディレクトリfdのfsyncをサポートしない環境がある。
            pass
    finally:
        os.close(fd)


def atomic_replace_file(
    path: Path,
    writer: Callable[[Path], None],
    *,
    validator: Callable[[Path], None] | None = None,
) -> Path:
    """writerの出力を検証してから``path``へ原子的に公開する.

    ``writer`` は引数の一時パスへ完全なファイルを書き込んでから戻ること。
    検証またはreplaceが失敗した場合、既存の最終ファイルは変更せず、一時
    ファイルだけを削除する。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        writer(temporary_path)
        if not temporary_path.is_file():
            raise OSError(f"atomic writer did not create a file: {temporary_path}")
        if validator is not None:
            validator(temporary_path)
        temporary_path.replace(path)
        _fsync_directory(path.parent)
        return path
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(
    path: Path,
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> Path:
    """UTF-8 JSONを同一ディレクトリの一時ファイル経由で保存する."""

    def _write(temporary_path: Path) -> None:
        with temporary_path.open("w", encoding="utf-8", newline="") as stream:
            json.dump(
                data,
                stream,
                ensure_ascii=ensure_ascii,
                indent=indent,
            )
            stream.flush()
            os.fsync(stream.fileno())

    return atomic_replace_file(path, _write)


def _validate_wav(path: Path) -> None:
    """waveモジュールで読み取れるWAVであることを検証する."""
    try:
        with wave.open(str(path), "rb") as stream:
            stream.getparams()
    except (EOFError, OSError, wave.Error) as exc:
        raise ValueError(f"invalid WAV file: {path}") from exc


def atomic_write_wav(
    path: Path, audio_data: bytes | bytearray | Callable[[Path], None]
) -> Path:
    """WAVを検証後に最終パスへ原子的に保存する.

    ``audio_data`` はWAVバイト列、または一時パスへWAVを書き込むwriterを受け取る。
    """

    if callable(audio_data):
        return atomic_replace_file(path, audio_data, validator=_validate_wav)

    def _write(temporary_path: Path) -> None:
        with temporary_path.open("wb") as stream:
            stream.write(audio_data)
            stream.flush()
            os.fsync(stream.fileno())

    return atomic_replace_file(path, _write, validator=_validate_wav)
