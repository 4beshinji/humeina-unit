"""Batch processing pipeline for web novel TTS synthesis."""

from .identity import (
    build_identity,
    config_fingerprint,
    legacy_url_work_id,
    new_job_id,
    new_run_id,
    plan_id_for_source,
    work_id_for_source,
)

__all__ = [
    "build_identity",
    "config_fingerprint",
    "legacy_url_work_id",
    "new_job_id",
    "new_run_id",
    "plan_id_for_source",
    "work_id_for_source",
]
