"""Compatibility facade for the RUN-001 manifest schema adapter."""

from collections.abc import Mapping

from .manifest import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    BatchManifest,
    ManifestAdapter,
    ManifestSchemaAdapter,
)


def load_manifest(
    base_dir, work_id: str, run_id: str | None = None
) -> BatchManifest:
    """Functional compatibility facade for callers outside ``batch.manifest``."""

    return BatchManifest.load(base_dir, work_id, run_id)


def load_manifest_for_url(base_dir, url: str) -> BatchManifest:
    return BatchManifest.load_for_url(base_dir, url)


def import_manifest_to_plan(
    manifest: BatchManifest | Mapping[str, object],
    *,
    config: object = None,
    merge_adjacent: bool = True,
):
    """旧BatchManifestをメモリ上のcanonical UtterancePlanへ変換する。

    変換は読み取り専用で、legacy manifestや音声成果物を書き換えない。JSON mappingを
    直接渡した場合も同じschema adapterを先に通すため、未知future schemaを黙って受理しない。
    """

    value = manifest
    if isinstance(value, Mapping):
        value = ManifestSchemaAdapter.from_dict(value)
    if not isinstance(value, BatchManifest):
        raise TypeError("manifest must be a BatchManifest or mapping")
    return value.to_utterance_plan(config=config, merge_adjacent=merge_adjacent)

__all__ = [
    "CURRENT_MANIFEST_SCHEMA_VERSION",
    "ManifestAdapter",
    "ManifestSchemaAdapter",
    "import_manifest_to_plan",
    "load_manifest",
    "load_manifest_for_url",
]
