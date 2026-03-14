"""Tests for character database."""

import json
import tempfile
from pathlib import Path

from yomiage.reader.character_db import CharacterDB


def test_get_or_create():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = CharacterDB("test_work", data_dir=Path(tmpdir))
        char = db.get_or_create("太郎", {"gender": "male", "age_group": "young_adult"})
        assert char.name == "太郎"
        assert char.gender == "male"
        assert "太郎" in db.known_names


def test_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        db1 = CharacterDB("test_work", data_dir=Path(tmpdir))
        db1.get_or_create("太郎", {"gender": "male"})

        db2 = CharacterDB("test_work", data_dir=Path(tmpdir))
        assert "太郎" in db2.known_names
        assert db2.characters["太郎"].gender == "male"


def test_lock_voice():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = CharacterDB("test_work", data_dir=Path(tmpdir))
        db.get_or_create("太郎")
        db.lock_voice("太郎", "47")
        assert db.characters["太郎"].voice_id == "47"
        assert db.characters["太郎"].voice_locked is True


def test_assign_voice():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = CharacterDB("test_work", data_dir=Path(tmpdir))
        db.get_or_create("太郎", {"gender": "male"})
        voices = [
            {"id": "1", "gender": "male", "age_group": "adult"},
            {"id": "2", "gender": "female", "age_group": "young_adult"},
        ]
        vid = db.assign_voice("太郎", voices)
        assert vid == "1"  # male match


def test_no_duplicate_voice():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = CharacterDB("test_work", data_dir=Path(tmpdir))
        db.get_or_create("太郎", {"gender": "male"})
        db.get_or_create("次郎", {"gender": "male"})
        voices = [
            {"id": "1", "gender": "male"},
            {"id": "2", "gender": "male"},
        ]
        db.assign_voice("太郎", voices)
        vid = db.assign_voice("次郎", voices)
        assert vid == "2"  # 1 already used by 太郎
