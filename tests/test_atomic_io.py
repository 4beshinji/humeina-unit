"""JSON/WAV atomic IOの契約テスト."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from yomiage.tts.audio_utils import write_silence_wav
from yomiage.utils.atomic_io import atomic_write_json, atomic_write_wav


def _temporary_files(directory: Path, name: str) -> list[Path]:
    return list(directory.glob(f".{name}.*.tmp"))


def test_atomic_json_is_utf8_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    atomic_write_json(path, {"message": "日本語", "value": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "message": "日本語",
        "value": 1,
    }
    assert _temporary_files(tmp_path, path.name) == []


def test_replace_failure_keeps_previous_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"old": true}', encoding="utf-8")

    def fail_replace(_self: Path, _target: Path) -> Path:
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        atomic_write_json(path, {"new": True})

    assert path.read_text(encoding="utf-8") == '{"old": true}'
    assert _temporary_files(tmp_path, path.name) == []


def test_concurrent_json_writes_are_atomic_and_last_writer_wins(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"

    def write(value: int) -> None:
        atomic_write_json(path, {"value": value})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(20)))

    assert json.loads(path.read_text(encoding="utf-8"))["value"] in range(20)
    assert _temporary_files(tmp_path, path.name) == []


def test_atomic_wav_validates_before_replace(tmp_path: Path) -> None:
    path = tmp_path / "audio.wav"
    write_silence_wav(path, duration=0.01)
    previous = path.read_bytes()

    with pytest.raises(ValueError, match="invalid WAV"):
        atomic_write_wav(path, b"not a wav")

    assert path.read_bytes() == previous
    assert _temporary_files(tmp_path, path.name) == []
