#!/usr/bin/env python3
"""停止中の VM の共有元だけを変更する。通常は差分表示、--apply で適用。"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def update_shared_directory(xml: str, shared_dir: Path, tag: str) -> str:
    """mount tag で対象を特定し、ディスクや他の共有設定を保持する。"""
    if not shared_dir.is_absolute():
        raise ValueError("shared directory must be an absolute path")
    root = ET.fromstring(xml)
    matches = [
        filesystem for filesystem in root.findall("./devices/filesystem")
        if filesystem.find("target") is not None
        and filesystem.find("target").get("dir") == tag
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one filesystem with mount tag {tag!r}")
    filesystem = matches[0]
    driver = filesystem.find("driver")
    source = filesystem.find("source")
    if driver is None or driver.get("type") != "virtiofs" or source is None:
        raise ValueError("target filesystem must use virtiofs with a source directory")
    if "dir" not in source.attrib:
        raise ValueError("external virtiofsd sockets are not managed by this command")
    if source.get("dir") == str(shared_dir):
        return xml
    source.set("dir", str(shared_dir))
    return ET.tostring(root, encoding="unicode") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="win11-voisona")
    parser.add_argument("--uri", default="qemu:///system")
    parser.add_argument("--tag", default="voisona_output")
    parser.add_argument("--shared-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    shared_dir = args.shared_dir.expanduser()
    if not shared_dir.is_absolute() or not shared_dir.is_dir():
        parser.error("--shared-dir must be an existing absolute directory")
    if shared_dir.is_symlink():
        parser.error("--shared-dir must be a real directory, not a checkout symlink")

    def virsh(*arguments: str) -> str:
        return subprocess.run(
            ["virsh", "-c", args.uri, *arguments], check=True, text=True,
            capture_output=True, env={**os.environ, "LC_ALL": "C"},
        ).stdout

    original = virsh("dumpxml", args.domain, "--inactive")
    updated = update_shared_directory(original, shared_dir, args.tag)
    if original == updated:
        return
    if not args.apply:
        for line in difflib.unified_diff(
            original.splitlines(), updated.splitlines(), fromfile="current", tofile="proposed",
        ):
            sys.stdout.write(line + "\n")
        return
    if args.backup is None:
        parser.error("--apply requires --backup with a new backup file path")
    if virsh("domstate", args.domain).strip() != "shut off":
        parser.error("VM must be shut off before applying this change")
    # x mode prevents accidentally overwriting an earlier rollback point.
    with args.backup.open("x", encoding="utf-8") as backup:
        backup.write(original)
    with tempfile.TemporaryDirectory(prefix="voisona-vm-") as temporary:
        definition = Path(temporary) / "domain.xml"
        definition.write_text(updated, encoding="utf-8")
        virsh("define", str(definition), "--validate")


if __name__ == "__main__":
    main()
