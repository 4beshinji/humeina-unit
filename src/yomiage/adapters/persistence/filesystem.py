"""ローカルfilesystem向けArtifactStore adapter。

一時ファイルは最終ファイルと同じディレクトリへ作り、検証後に ``Path.replace`` で公開
する。manifestも同じ原子的JSON writerを通るため、プロセス中断時に壊れた最終成果物を
列挙しない。
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
import wave
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ...ports.artifacts import (
    ARTIFACT_MANIFEST_FILENAME,
    ArtifactIntegrityError,
    ArtifactIssue,
    ArtifactManifest,
    ArtifactRecord,
    ArtifactRef,
    ArtifactValidationError,
)
from ...runtime_paths import output_dir as default_output_dir
from ...utils.atomic_io import atomic_replace_file, atomic_write_json


class FilesystemArtifactStore:
    """``output/works/{work_id}/runs/{run_id}``を管理するartifact store。"""

    def __init__(
        self, base_dir: Path | str | None = None, *, output_dir: Path | str | None = None
    ):
        if output_dir is not None:
            if base_dir is not None and Path(base_dir) != Path(output_dir):
                raise ValueError("base_dir and output_dir must match")
            base_dir = output_dir
        self.base_dir = Path(base_dir or default_output_dir()).expanduser().resolve()

    # ------------------------------------------------------------------
    # Run/path boundary
    # ------------------------------------------------------------------
    @staticmethod
    def _component(value: str, name: str) -> str:
        value = str(value)
        if not value or Path(value).name != value or value in {".", ".."}:
            raise ValueError(f"{name} must be a non-empty filesystem component")
        return value

    def run_dir(self, work_id: str, run_id: str) -> Path:
        """canonicalなrun directoryを作成して返す。"""

        work = self._component(work_id, "work_id")
        run = self._component(run_id, "run_id")
        result = self.base_dir / "works" / work / "runs" / run
        result.mkdir(parents=True, exist_ok=True)
        return result

    def run_directory(self, work_id: str, run_id: str) -> Path:
        """``run_dir``の説明的alias。"""

        return self.run_dir(work_id, run_id)

    def artifact_manifest_path(self, work_id: str, run_id: str) -> Path:
        return self.run_dir(work_id, run_id) / ARTIFACT_MANIFEST_FILENAME

    def _resolve_destination(
        self,
        destination: str | Path,
        *,
        work_id: str | None = None,
        run_id: str | None = None,
        source: Path | None = None,
    ) -> tuple[Path, str]:
        if work_id is not None or run_id is not None:
            if work_id is None or run_id is None:
                raise ValueError("work_id and run_id must be supplied together")
            root = self.run_dir(work_id, run_id)
        else:
            destination_path = Path(destination)
            if destination_path.is_absolute():
                root = self._find_run_root(destination_path)
            elif source is not None:
                root = self._find_run_root(source)
            else:
                raise ValueError("relative destination requires work_id/run_id or a run source")

        path = destination if isinstance(destination, Path) else Path(destination)
        if path.is_absolute():
            final = path.resolve()
        else:
            if any(part in {"", ".", ".."} for part in path.parts):
                raise ValueError("artifact path must not contain empty or traversal components")
            final = (root / path).resolve()
        try:
            relative = final.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError("artifact path must stay inside the run directory") from exc
        if not relative or relative == ARTIFACT_MANIFEST_FILENAME:
            raise ValueError("artifact destination is not publishable")
        return final, relative

    def _find_run_root(self, path: Path) -> Path:
        """pathからcanonical run rootを取り出し、base_dir外を拒否する。"""

        resolved = Path(path).expanduser().resolve()
        try:
            relative = resolved.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("artifact path is outside the configured base directory") from exc
        parts = relative.parts
        if len(parts) < 4 or parts[0] != "works" or parts[2] != "runs":
            raise ValueError("artifact path must be inside works/{work_id}/runs/{run_id}")
        return (self.base_dir / "works" / parts[1] / "runs" / parts[3]).resolve()

    # ------------------------------------------------------------------
    # Atomic temp/publish operations
    # ------------------------------------------------------------------
    @contextmanager
    def temporary_artifact(
        self,
        work_id: str | Path,
        run_id: str | Path,
        relative_path: str | Path | None = None,
    ) -> Iterator[Path]:
        """final pathと同じディレクトリに一時artifactを作る。

        Canonical callers pass ``work_id, run_id, relative_path``.  For adapters that already
        resolved a run directory, ``run_dir, relative_path`` is also accepted; both forms still
        enforce that the target remains under the configured artifact root.
        """

        if relative_path is None:
            run_root = Path(work_id).expanduser().resolve()
            relative_path = run_id
            try:
                run_root = self._find_run_root(run_root)
            except ValueError as exc:
                raise ValueError("run directory is outside the configured base directory") from exc
            relative = Path(relative_path)
            if relative.is_absolute() or any(
                part in {"", ".", ".."} for part in relative.parts
            ):
                raise ValueError("artifact path must not contain empty or traversal components")
            final = (run_root / relative).resolve()
            try:
                final.relative_to(run_root)
            except ValueError as exc:
                raise ValueError("artifact path must stay inside the run directory") from exc
        else:
            final, _ = self._resolve_destination(
                relative_path,
                work_id=str(work_id),
                run_id=str(run_id),
            )

        final.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix=f".{final.name}.", suffix=".tmp", dir=final.parent
        )
        os.close(fd)
        temporary = Path(name)
        try:
            yield temporary
        finally:
            temporary.unlink(missing_ok=True)

    # A noun-oriented alias is useful to callers that avoid contextmanager terminology.
    temporary_path = temporary_artifact
    temporary_file = temporary_artifact
    temp_artifact = temporary_artifact
    temporary = temporary_artifact

    def publish(
        self,
        source: Path,
        destination: str | Path,
        *,
        artifact_id: str | None = None,
        provider: str | None = None,
        duration: float | None = None,
        validate: bool = True,
        work_id: str | None = None,
        run_id: str | None = None,
    ) -> ArtifactRef:
        """sourceを検証してdestinationへ原子的に公開する。"""

        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise ArtifactValidationError(f"artifact source does not exist: {source}")
        if duration is not None:
            duration = float(duration)
            if not math.isfinite(duration) or duration < 0:
                raise ArtifactValidationError(
                    "artifact duration must be a finite, non-negative number"
                )
        final, relative = self._resolve_destination(
            destination, work_id=work_id, run_id=run_id, source=source
        )
        final.parent.mkdir(parents=True, exist_ok=True)

        is_wav = final.suffix.lower() == ".wav" or source.suffix.lower() == ".wav"
        # WAVはvalidate=Falseでも検証を省略しない。音声を未検証のままfinal artifactへ
        # 列挙しないことがRUN-002の不変条件であり、validateは将来の非WAV形式向けに残す。
        validator = self._validate_wav if is_wav else None
        try:
            if source == final:
                # Provider互換のためsource==destinationも受け付けるが、最終パスを
                # 検証前の書き込み途中状態へ戻さないよう、いったん同一ディレクトリの
                # tempへcopyしてからreplaceする。
                def _copy_existing(temporary: Path) -> None:
                    with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
                        shutil.copyfileobj(input_stream, output_stream)
                        output_stream.flush()
                        os.fsync(output_stream.fileno())

                atomic_replace_file(final, _copy_existing, validator=validator)
            else:
                def _copy(temporary: Path) -> None:
                    with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
                        shutil.copyfileobj(input_stream, output_stream)
                        output_stream.flush()
                        os.fsync(output_stream.fileno())

                atomic_replace_file(final, _copy, validator=validator)
                source.unlink(missing_ok=True)
        except (OSError, ValueError, wave.Error) as exc:
            raise ArtifactValidationError(f"artifact validation failed: {source}") from exc

        digest = self.checksum(final)
        if duration is None and is_wav:
            duration = self._wav_duration(final)
        return ArtifactRef(
            artifact_id=artifact_id or relative,
            relative_path=relative,
            path=final,
            checksum=digest,
            size_bytes=final.stat().st_size,
            duration=duration,
            provider=provider,
            verified=True,
        )

    @staticmethod
    def _validate_wav(path: Path) -> None:
        try:
            with wave.open(str(path), "rb") as stream:
                params = stream.getparams()
                if params.framerate <= 0 or params.nchannels <= 0:
                    raise ValueError("WAV has invalid format")
        except (EOFError, OSError, wave.Error, ValueError) as exc:
            raise ArtifactValidationError(f"invalid WAV artifact: {path}") from exc

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as stream:
                rate = stream.getframerate()
                return stream.getnframes() / rate if rate else 0.0
        except (EOFError, OSError, wave.Error):
            return 0.0

    # ------------------------------------------------------------------
    # File metadata and manifest operations
    # ------------------------------------------------------------------
    def exists(self, target: Path | ArtifactRef) -> bool:
        if isinstance(target, ArtifactRef):
            target = target.path
        return Path(target).is_file()

    def checksum(self, target: Path | ArtifactRef) -> str:
        path = target.path if isinstance(target, ArtifactRef) else Path(target)
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def load_manifest(self, work_id: str, run_id: str) -> ArtifactManifest:
        path = self.artifact_manifest_path(work_id, run_id)
        if not path.is_file():
            return ArtifactManifest(work_id=work_id, run_id=run_id)
        import json

        return ArtifactManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_manifest(self, manifest: ArtifactManifest) -> Path:
        path = self.artifact_manifest_path(manifest.work_id, manifest.run_id)
        atomic_write_json(path, manifest.to_dict())
        return path

    # Read/write terminology is convenient for composition roots and keeps the adapter boundary
    # independent from the on-disk JSON filename.
    read_manifest = load_manifest
    write_manifest = save_manifest

    def initialize_manifest(
        self,
        work_id: str,
        run_id: str,
        utterance_ids: list[str] | tuple[str, ...],
        *,
        provider: str | None = None,
    ) -> ArtifactManifest:
        manifest = self.load_manifest(work_id, run_id)
        for utterance_id in utterance_ids:
            existing = manifest.get(str(utterance_id))
            if existing is None:
                manifest.upsert(ArtifactRecord(str(utterance_id), provider=provider))
            elif existing.provider is None and provider is not None:
                existing.provider = provider
        self.save_manifest(manifest)
        return manifest

    def record_utterance(
        self,
        work_id: str,
        run_id: str,
        utterance_id: str,
        *,
        status: str,
        artifact: ArtifactRef | None = None,
        duration: float | None = None,
        provider: str | None = None,
        error: str | None = None,
    ) -> ArtifactRecord:
        """Utterance状態を更新する。artifactが検証済みの場合だけfinalへ列挙する。"""

        manifest = self.load_manifest(work_id, run_id)
        record = ArtifactRecord(
            utterance_id=str(utterance_id),
            status=status,
            path=artifact.relative_path if artifact and artifact.verified else None,
            checksum=artifact.checksum if artifact and artifact.verified else None,
            duration=(
                duration if duration is not None else (artifact.duration if artifact else None)
            ),
            provider=(
                provider if provider is not None else (artifact.provider if artifact else None)
            ),
            size_bytes=artifact.size_bytes if artifact and artifact.verified else None,
            verified=bool(artifact and artifact.verified),
            error=error,
        )
        manifest.upsert(record)
        self.save_manifest(manifest)
        return record

    def verify_manifest(
        self,
        work_id: str,
        run_id: str,
        *,
        strict: bool = False,
    ) -> tuple[ArtifactIssue, ...]:
        manifest = self.load_manifest(work_id, run_id)
        issues = manifest.verify(self.run_dir(work_id, run_id), self.checksum)
        if strict and issues:
            raise ArtifactIntegrityError(issues)
        return issues

    def assert_manifest_integrity(self, work_id: str, run_id: str) -> None:
        issues = self.verify_manifest(work_id, run_id)
        if issues:
            raise ArtifactIntegrityError(issues)

    # Friendly aliases used by migration callers.
    check_manifest = verify_manifest
    verify = verify_manifest


# spelling aliases keep adapter construction discoverable for callers that use the common
# ``FileSystem`` capitalization while the canonical class follows ``filesystem`` module naming.
FileSystemArtifactStore = FilesystemArtifactStore
LocalArtifactStore = FilesystemArtifactStore

__all__ = ["FileSystemArtifactStore", "FilesystemArtifactStore", "LocalArtifactStore"]
