"""CFG-002 package-owned standard resource contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from yomiage.config import load_config, load_settings
from yomiage.resource_loader import packaged_config_filenames, packaged_config_resource


def test_standard_resources_are_loaded_through_importlib_resources() -> None:
    default = packaged_config_resource("default.yaml")
    scene = packaged_config_resource("scene_params.yaml")
    math = packaged_config_resource("math_dict.yaml")

    assert default.is_file()
    assert scene.is_file()
    assert math.is_file()
    config = load_config(env={})
    assert config["scene_params"]["scenes"]
    assert config["math_dict"]["unicode_symbols"]


@pytest.mark.parametrize("name", packaged_config_filenames())
def test_every_standard_resource_is_present(name: str) -> None:
    """The wheel's immutable bundle must contain every declared YAML resource."""

    resource = packaged_config_resource(name)
    assert resource.is_file()
    assert resource.read_text(encoding="utf-8").strip()


def test_unknown_standard_resource_is_rejected() -> None:
    """Callers cannot accidentally treat user profiles as package resources."""

    with pytest.raises(ValueError, match="unknown packaged configuration resource"):
        packaged_config_resource("voice_profiles/custom.yaml")


def test_missing_standard_bundle_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken installation must fail loudly instead of returning a partial config."""

    import yomiage.config as config_module

    def missing_resource(name: str) -> dict:
        raise FileNotFoundError(name)

    monkeypatch.setattr(config_module, "packaged_config_yaml", missing_resource)
    with pytest.raises(FileNotFoundError, match="packaged configuration resources"):
        config_module.load_config(env={})


def test_packaged_default_contains_no_machine_specific_absolute_paths() -> None:
    for name in packaged_config_filenames():
        text = packaged_config_resource(name).read_text(encoding="utf-8")
        assert "/home/" not in text
        assert "/Users/" not in text
        assert "Voicepeak-linux64" not in text


def test_user_config_path_is_separate_from_packaged_resources(tmp_path: Path) -> None:
    user = tmp_path / "user-config"
    raw = load_config(user_config_dir=user, env={})
    settings = load_settings(
        env={},
        user_config_dir=user,
        base_dir=tmp_path,
        validate_active=False,
    )
    assert settings.storage.user_config_dir == user.resolve()
    assert settings.storage.packaged_config_dir != settings.storage.user_config_dir
    assert "resources" in str(settings.storage.packaged_config_dir)
    assert Path(raw["batch"]["voice_profile_dir"]) == user / "voice_profiles"
