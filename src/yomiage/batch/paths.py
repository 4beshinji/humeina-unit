"""Filesystem paths for work, plan, and run artifacts.

RUN-001 introduces ``works/{work_id}/plans`` and ``works/{work_id}/runs/{run_id}`` while keeping
the historic ``{base}/{work_id}`` directory readable.  This module owns path resolution so callers
do not need to guess which layout a manifest uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..core.identity import legacy_url_work_id, normalize_url


@dataclass(frozen=True, slots=True, init=False)
class BatchPaths:
    """Canonical and legacy paths for one work/run pair."""

    base_dir: Path
    work_id: str
    run_id: str | None = None
    plan_id_value: str | None = None

    def __init__(
        self,
        base_dir: Path | str,
        work_id: str,
        run_id: str | None = None,
        plan_id: str | None = None,
        *,
        plan_id_value: str | None = None,
    ) -> None:
        """Create paths while accepting ``plan_id`` as the public keyword name."""

        if plan_id is not None and plan_id_value is not None and plan_id != plan_id_value:
            raise ValueError("plan_id and plan_id_value must match")
        object.__setattr__(self, "base_dir", Path(base_dir))
        object.__setattr__(self, "work_id", work_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "plan_id_value", plan_id if plan_id is not None else plan_id_value)
        self._validate()

    def _validate(self) -> None:
        if not self.work_id or Path(self.work_id).name != self.work_id:
            raise ValueError("work_id must be a non-empty filesystem name")
        if self.run_id is not None and (
            not self.run_id or Path(self.run_id).name != self.run_id
        ):
            raise ValueError("run_id must be a filesystem name")
        if self.plan_id_value is not None and (
            not self.plan_id_value or Path(self.plan_id_value).name != self.plan_id_value
        ):
            raise ValueError("plan_id must be a filesystem name")

    @property
    def works_dir(self) -> Path:
        return self.base_dir / "works"

    @property
    def work_dir(self) -> Path:
        """Canonical work root (``output/works/{work_id}``)."""

        return self.works_dir / self.work_id

    @property
    def plans_dir(self) -> Path:
        return self.work_dir / "plans"

    @property
    def plan_dir(self) -> Path:
        """Singular alias for :attr:`plans_dir`."""

        return self.plans_dir

    @property
    def runs_dir(self) -> Path:
        return self.work_dir / "runs"

    @property
    def run_root(self) -> Path:
        return self.runs_dir

    @property
    def run_dir(self) -> Path:
        """Canonical run directory; raises if no run was selected."""

        if self.run_id is None:
            raise ValueError("run_id is required for a run directory")
        return self.runs_dir / self.run_id

    @property
    def plan_path(self) -> Path:
        if self.plan_id_value is None:
            raise ValueError("plan_id is required for a plan path")
        return self.plans_dir / f"{self.plan_id_value}.json"

    @property
    def plan_id(self) -> str | None:
        """Explicit plan ID, when this object was created with :meth:`for_plan`."""

        return self.plan_id_value

    @property
    def work_path(self) -> Path:
        """Alias for :attr:`work_dir` used by path-oriented callers."""

        return self.work_dir

    @property
    def run_path(self) -> Path:
        """Alias for :attr:`run_dir`."""

        return self.run_dir

    def plan_path_for(self, plan_id: str) -> Path:
        self._validate_component(plan_id, "plan_id")
        return self.plans_dir / f"{plan_id}.json"

    @property
    def manifest_path(self) -> Path:
        if self.run_id is None:
            raise ValueError("run_id is required for a canonical manifest path")
        return self.run_dir / "manifest.json"

    @property
    def artifact_manifest_path(self) -> Path:
        if self.run_id is None:
            raise ValueError("run_id is required for a canonical artifact manifest path")
        return self.run_dir / "artifact-manifest.json"

    @property
    def legacy_work_dir(self) -> Path:
        """Historic work root (``output/{work_id}``)."""

        return self.base_dir / self.work_id

    @property
    def legacy_dir(self) -> Path:
        return self.legacy_work_dir

    @property
    def legacy_manifest_path(self) -> Path:
        return self.legacy_work_dir / "manifest.json"

    @staticmethod
    def _validate_component(value: str, name: str) -> None:
        if not value or Path(value).name != value:
            raise ValueError(f"{name} must be a non-empty filesystem name")

    @classmethod
    def canonical(cls, base_dir: Path | str, work_id: str, run_id: str) -> "BatchPaths":
        """Construct paths for a new run."""

        return cls(Path(base_dir), work_id, run_id=run_id)

    @classmethod
    def for_plan(
        cls,
        base_dir: Path | str,
        work_id: str,
        plan_id: str,
        run_id: str | None = None,
    ) -> "BatchPaths":
        """Construct a path object with an explicit plan ID."""

        return cls(Path(base_dir), work_id, run_id=run_id, plan_id_value=plan_id)

    @classmethod
    def legacy(cls, base_dir: Path | str, work_id: str) -> "BatchPaths":
        """Construct a compatibility path object for an old manifest."""

        return cls(Path(base_dir), work_id, None)

    @classmethod
    def find_manifest(
        cls,
        base_dir: Path | str,
        work_id: str,
        run_id: str | None = None,
    ) -> Path:
        """Resolve a manifest in the new layout, then fall back to the old layout.

        Without an explicit ``run_id`` the newest canonical run is selected by modification time.
        BatchJobManager always passes the run ID, so concurrent requests never depend on this
        fallback selection.
        """

        paths = cls(Path(base_dir), work_id, run_id=run_id)
        if run_id is not None:
            candidates = (
                paths.manifest_path,
                Path(base_dir) / "works" / work_id / "runs" / run_id / "manifest.json",
                Path(base_dir) / work_id / "runs" / run_id / "manifest.json",
            )
        else:
            candidates = ()
        for candidate in candidates:
            if candidate.is_file():
                return candidate

        canonical_root = paths.work_dir
        if run_id is None and canonical_root.is_dir():
            runs_root = canonical_root / "runs"
            runs = sorted(
                (path for path in runs_root.glob("*/manifest.json") if path.is_file()),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            if runs:
                return runs[0]
            direct = canonical_root / "manifest.json"
            if direct.is_file():
                return direct

        legacy = paths.legacy_manifest_path
        if legacy.is_file():
            return legacy
        raise FileNotFoundError(f"Manifest not found for work_id: {work_id}")

    @classmethod
    def find_manifest_for_url(
        cls,
        base_dir: Path | str,
        url: str,
        *,
        legacy_only: bool = False,
    ) -> Path:
        """Resolve a manifest from a source URL during legacy migration.

        New manifests are normally addressed by their explicit ``work_id``/``run_id``.  This URL
        helper is intentionally read-only and first checks the historic MD5 directory, then scans
        canonical manifests for a matching normalized source URL.
        """

        base = Path(base_dir)
        legacy = base / legacy_url_work_id(url) / "manifest.json"
        if legacy.is_file():
            return legacy
        if legacy_only:
            raise FileNotFoundError(f"Manifest not found for URL: {url}")
        normalized = normalize_url(url)
        root = base / "works"
        for candidate in root.glob("*/runs/*/manifest.json"):
            try:
                data = json.loads(candidate.read_text())
                if normalize_url(str(data.get("source_url", ""))) == normalized:
                    return candidate
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        raise FileNotFoundError(f"Manifest not found for URL: {url}")

    @classmethod
    def run_dirs(cls, base_dir: Path | str, work_id: str) -> Iterable[Path]:
        """Yield canonical run directories in newest-first order."""

        root = cls(Path(base_dir), work_id, None).runs_dir
        if not root.is_dir():
            return ()
        return tuple(
            sorted(
                (path for path in root.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        )


def run_dir(base_dir: Path | str, work_id: str, run_id: str) -> Path:
    """Small functional facade used by callers that do not need ``BatchPaths``."""

    return BatchPaths.canonical(base_dir, work_id, run_id).run_dir


def plan_path(base_dir: Path | str, work_id: str, plan_id: str) -> Path:
    return BatchPaths(Path(base_dir), work_id).plan_path_for(plan_id)


def legacy_manifest_path(base_dir: Path | str, work_id: str) -> Path:
    return BatchPaths.legacy(base_dir, work_id).legacy_manifest_path


__all__ = [
    "BatchPaths",
    "legacy_manifest_path",
    "plan_path",
    "run_dir",
]
