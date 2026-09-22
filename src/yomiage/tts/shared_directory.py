"""VoiSona の共有領域を最終成果物の保存先から分離する。"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Iterator

from ..runtime_paths import data_dir


def windows_mount(value: str) -> PureWindowsPath:
    """旧設定の ``Z:`` もドライブ直下として解釈する。"""
    if len(value) == 2 and value[1] == ":":
        value += "\\"
    path = PureWindowsPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("voisona.vm_mount must be an absolute Windows drive or UNC path")
    return path


@dataclass(frozen=True)
class VoisonaSharedDirectory:
    """libvirt の source dir と Windows マウントを明示的に対応付ける。"""

    host_root: Path
    guest_root: PureWindowsPath

    @classmethod
    def from_config(cls, config: dict | None = None) -> VoisonaSharedDirectory:
        config = config or {}
        host = Path(config.get("shared_dir") or os.environ.get(
            "VOISONA_SHARED_DIR", data_dir() / "voisona"
        )).expanduser()
        if not host.is_absolute():
            raise ValueError("voisona.shared_dir must be an absolute host path")
        mount = str(config.get("vm_mount") or os.environ.get("VOISONA_VM_MOUNT", "Z:"))
        return cls(host.resolve(), windows_mount(mount))

    @contextmanager
    def temporary_wav(self) -> Iterator[tuple[Path, str]]:
        """呼び出しごとに異なる共有ファイルを割り当て、読み取り後に削除する。"""
        if not self.host_root.is_dir():
            raise FileNotFoundError(
                f"VoiSona shared directory does not exist: {self.host_root}; "
                "configure voisona.shared_dir / VOISONA_SHARED_DIR to match libvirt source dir"
            )
        filename = f"yomiage-{uuid.uuid4().hex}.wav"
        host_path = self.host_root / filename
        try:
            yield host_path, str(self.guest_root / filename)
        finally:
            host_path.unlink(missing_ok=True)
