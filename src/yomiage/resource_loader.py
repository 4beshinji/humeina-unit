"""Package-owned resources used by the default configuration.

The checkout historically kept the default YAML files in ``config/``.  That
directory is not part of an installed wheel, and resolving it from the current
working directory makes an installation depend on the repository layout.  The
files under ``yomiage/resources/config`` are immutable package resources;
user-editable settings and profiles are loaded from a separate directory by
``yomiage.config``.

Use :mod:`importlib.resources` rather than converting a resource to a
filesystem path.  Wheels are normally unpacked, but the ``Traversable`` API
also remains correct for other import loaders.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

import yaml

_RESOURCE_ROOT: Final[Traversable] = files("yomiage").joinpath("resources", "config")
_CONFIG_FILENAMES: Final[tuple[str, ...]] = (
    "default.yaml",
    "voices.yaml",
    "scene_params.yaml",
    "math_dict.yaml",
)


def packaged_config_resource(name: str) -> Traversable:
    """Return one immutable packaged configuration resource.

    Only the known standard resources are exposed.  User profiles must not be
    added below this directory; they belong to the user configuration layer.
    """

    if name not in _CONFIG_FILENAMES:
        raise ValueError(f"unknown packaged configuration resource: {name}")
    resource = _RESOURCE_ROOT.joinpath(name)
    if not resource.is_file():
        raise FileNotFoundError(f"packaged configuration resource is missing: {name}")
    return resource


def packaged_config_text(name: str) -> str:
    """Read a packaged YAML resource as UTF-8 text."""

    return packaged_config_resource(name).read_text(encoding="utf-8")


def packaged_config_yaml(name: str) -> dict:
    """Read and validate one packaged YAML mapping."""

    value = yaml.safe_load(packaged_config_text(name)) or {}
    if not isinstance(value, dict):
        raise ValueError(f"packaged configuration resource must contain a mapping: {name}")
    return value


def packaged_config_path() -> Path:
    """Return the conventional installed path for diagnostics and path metadata.

    Configuration *loading* uses :func:`packaged_config_resource`; this path is
    only a stable, absolute label for ``Settings.storage.packaged_config_dir``
    and for logs.  It can be absent for import loaders that do not expose files.
    """

    return Path(__file__).resolve().parent / "resources" / "config"


def packaged_config_filenames() -> tuple[str, ...]:
    """Return the names in the immutable standard configuration bundle."""

    return _CONFIG_FILENAMES


__all__ = [
    "packaged_config_filenames",
    "packaged_config_path",
    "packaged_config_resource",
    "packaged_config_text",
    "packaged_config_yaml",
]
