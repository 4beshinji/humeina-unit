"""成果物ストレージのポートと、artifact manifest の値オブジェクト。

バッチやStudioが ``output`` 配下のパスを直接組み立てると、途中まで書かれた音声や
別runの同名ファイルを誤って公開しやすい。このモジュールは、成果物の一時書き込み・
検証済み公開・完全性確認を実装から切り離すための小さな契約を定義する。

ここにはfilesystem実装を置かない。実装は ``adapters.persistence.filesystem`` にあり、
application/ports側は標準ライブラリの値型だけに依存する。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ContextManager, Mapping, Protocol, Sequence, runtime_checkable

ARTIFACT_MANIFEST_SCHEMA_VERSION = 1
ARTIFACT_MANIFEST_FILENAME = "artifact-manifest.json"

ARTIFACT_STATES = frozenset(
    {
        "pending",
        "synthesizing",
        "synthesized",
        "fallback_synthesized",
        "skipped",
        "failed",
    }
)
FINAL_ARTIFACT_STATES = frozenset({"synthesized", "fallback_synthesized"})


class ArtifactError(RuntimeError):
    """成果物の保存、検証、公開に関する基底例外。"""


class ArtifactValidationError(ArtifactError, ValueError):
    """一時ファイルが公開可能な成果物ではない。"""


class ArtifactIntegrityError(ArtifactError):
    """公開済み成果物が欠損または改変されている。"""

    def __init__(self, issues: Sequence["ArtifactIssue"]):
        self.issues = tuple(issues)
        details = "; ".join(f"{item.utterance_id}: {item.reason}" for item in self.issues)
        super().__init__(details or "artifact integrity check failed")


@dataclass(frozen=True, slots=True)
class ArtifactIssue:
    """manifestと実体の不一致を表す診断情報。"""

    utterance_id: str
    reason: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """検証を通過して公開された成果物への参照。

    ``path`` はadapterが返す実体パス、``relative_path`` はmanifestへ保存するrun相対パス
    である。呼び出し側はchecksumやverifiedを再計算せず、この参照を保存する。
    """

    artifact_id: str
    relative_path: str
    path: Path
    checksum: str
    size_bytes: int
    duration: float | None = None
    provider: str | None = None
    verified: bool = True

    @property
    def name(self) -> str:
        """manifest実装との互換用alias。"""

        return self.relative_path

    @property
    def sha256(self) -> str:
        """checksumの説明的なalias。"""

        return self.checksum

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "path": self.relative_path,
            "relative_path": self.relative_path,
            "checksum": self.checksum,
            "sha256": self.checksum,
            "size_bytes": self.size_bytes,
            "duration": self.duration,
            "provider": self.provider,
            "verified": self.verified,
        }


@dataclass(slots=True)
class ArtifactRecord:
    """1 Utteranceの実行状態と成果物メタデータ。"""

    utterance_id: str
    status: str = "pending"
    path: str | None = None
    checksum: str | None = None
    duration: float | None = None
    provider: str | None = None
    size_bytes: int | None = None
    verified: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        self.utterance_id = str(self.utterance_id).strip()
        if not self.utterance_id:
            raise ValueError("utterance_id must not be empty")
        self.status = str(self.status).strip().lower()
        if self.status not in ARTIFACT_STATES:
            raise ValueError(f"unsupported artifact state: {self.status}")
        # 未検証の音声はfinal artifactの一覧へ入れない。失敗・pendingのレコードに
        # stale metadataが渡ってきても公開側からは見えないように正規化する。
        if not self.verified or self.status not in FINAL_ARTIFACT_STATES:
            self.path = None
            self.checksum = None
            self.size_bytes = None
            self.verified = False
        if self.duration is not None:
            self.duration = float(self.duration)
            if not math.isfinite(self.duration) or self.duration < 0:
                raise ValueError("duration must be a finite, non-negative number")

    @property
    def is_final(self) -> bool:
        return bool(
            self.verified
            and self.path
            and self.checksum
            and self.status in FINAL_ARTIFACT_STATES
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactRecord":
        if not isinstance(data, Mapping):
            raise TypeError("artifact record must be a mapping")
        checksum = data.get("checksum", data.get("sha256"))
        path = data.get("path", data.get("relative_path"))
        return cls(
            utterance_id=str(data.get("utterance_id", data.get("id", ""))),
            status=str(data.get("status", "pending")),
            path=str(path) if path is not None else None,
            checksum=str(checksum) if checksum is not None else None,
            duration=data.get("duration"),
            provider=(str(data["provider"]) if data.get("provider") is not None else None),
            size_bytes=(int(data["size_bytes"]) if data.get("size_bytes") is not None else None),
            verified=bool(
                data.get(
                    "verified",
                    checksum is not None and path is not None,
                )
            ),
            error=(str(data["error"]) if data.get("error") is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "utterance_id": self.utterance_id,
            "status": self.status,
            "path": self.path,
            "checksum": self.checksum,
            "duration": self.duration,
            "provider": self.provider,
            "size_bytes": self.size_bytes,
            "verified": self.verified,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(slots=True)
class ArtifactManifest:
    """run単位で保存するartifact manifest。

    ``records`` はUtteranceの状態を全件保持する。一方 ``final_artifacts`` は検証済みで
    terminalな音声だけを返すため、未検証WAVが公開成果物として列挙されることはない。
    """

    work_id: str
    run_id: str
    records: list[ArtifactRecord] = field(default_factory=list)
    schema_version: int = ARTIFACT_MANIFEST_SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if not str(self.work_id).strip() or not str(self.run_id).strip():
            raise ValueError("work_id and run_id are required")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version <= 0
            or self.schema_version > ARTIFACT_MANIFEST_SCHEMA_VERSION
        ):
            raise ValueError(
                f"unsupported artifact manifest schema_version: {self.schema_version}"
            )
        self.work_id = str(self.work_id)
        self.run_id = str(self.run_id)
        self.records = [
            item if isinstance(item, ArtifactRecord) else ArtifactRecord.from_dict(item)
            for item in self.records
        ]

    @property
    def artifacts(self) -> list[ArtifactRecord]:
        """recordsの説明的なalias。"""

        return self.records

    @property
    def entries(self) -> list[ArtifactRecord]:
        """Manifest entry alias for callers that use the ``entries`` terminology."""

        return self.records

    @property
    def final_artifacts(self) -> tuple[ArtifactRecord, ...]:
        return tuple(item for item in self.records if item.is_final)

    @property
    def published_artifacts(self) -> tuple[ArtifactRecord, ...]:
        """``final_artifacts``の読みやすいalias。"""

        return self.final_artifacts

    def get(self, utterance_id: str) -> ArtifactRecord | None:
        key = str(utterance_id)
        return next((item for item in self.records if item.utterance_id == key), None)

    def upsert(self, record: ArtifactRecord) -> ArtifactRecord:
        existing = self.get(record.utterance_id)
        if existing is None:
            self.records.append(record)
        else:
            self.records[self.records.index(existing)] = record
        return record

    def to_dict(self) -> dict[str, Any]:
        records = [item.to_dict() for item in self.records]
        return {
            "schema_version": self.schema_version,
            "work_id": self.work_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "artifacts": records,
            # ``utterances``は初期実装のfixture/reader向けの明示的alias。読み込みでは
            # どちらか一方を受け付け、書き込みはcanonicalなartifactsを利用する。
            "utterances": records,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactManifest":
        if not isinstance(data, Mapping):
            raise TypeError("artifact manifest must be a mapping")
        raw_records = data.get("artifacts", data.get("utterances", []))
        if isinstance(raw_records, Mapping):
            raw_records = list(raw_records.values())
        if not isinstance(raw_records, Sequence) or isinstance(
            raw_records, (str, bytes, bytearray)
        ):
            raise TypeError("artifact manifest artifacts must be a sequence")
        schema = data.get("schema_version", ARTIFACT_MANIFEST_SCHEMA_VERSION)
        if isinstance(schema, str) and schema.isdigit():
            schema = int(schema)
        if not isinstance(schema, int) or schema <= 0:
            raise ValueError("artifact manifest schema_version must be a positive integer")
        if schema > ARTIFACT_MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported artifact manifest schema_version: {schema}")
        return cls(
            work_id=str(data.get("work_id", "")),
            run_id=str(data.get("run_id", "")),
            records=[ArtifactRecord.from_dict(item) for item in raw_records],
            schema_version=schema,
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
        )

    def verify(
        self,
        run_directory: Path,
        checksum_fn: Any | None = None,
    ) -> tuple[ArtifactIssue, ...]:
        """検証済みレコードの実体とchecksumを検査する。"""

        root = Path(run_directory).resolve()
        if checksum_fn is None:
            def checksum_fn(path: Path) -> str:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                return digest.hexdigest()

        issues: list[ArtifactIssue] = []
        for record in self.final_artifacts:
            if not record.path:
                issues.append(ArtifactIssue(record.utterance_id, "missing relative path"))
                continue
            candidate = (root / record.path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                issues.append(
                    ArtifactIssue(record.utterance_id, "path escapes run directory", record.path)
                )
                continue
            if not candidate.is_file():
                issues.append(ArtifactIssue(record.utterance_id, "missing artifact", record.path))
                continue
            actual = checksum_fn(candidate)
            if actual != record.checksum:
                issues.append(ArtifactIssue(record.utterance_id, "checksum mismatch", record.path))
        return tuple(issues)


@runtime_checkable
class ArtifactStore(Protocol):
    """成果物保存adapterが満たす最小契約。"""

    def run_dir(self, work_id: str, run_id: str) -> Path:
        """canonicalなrun directoryを返し、必要なら作成する。"""

    def temporary_artifact(
        self, work_id: str, run_id: str, relative_path: str | Path
    ) -> ContextManager[Path]:
        """同一ディレクトリへ一時artifactを作成する。"""

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
        """検証済みartifactをfinal pathへ公開する。"""

    def exists(self, target: Path | ArtifactRef) -> bool:
        """artifact実体の存在を確認する。"""

    def checksum(self, target: Path | ArtifactRef) -> str:
        """artifactのSHA-256を返す。"""


# 初期実装の呼び出し側で採用されやすい名称を値型のaliasとして公開する。実体は
# ArtifactRecordのままなので、manifest schemaのフィールドは一つに保たれる。
ArtifactMetadata = ArtifactRecord
ArtifactManifestEntry = ArtifactRecord


__all__ = [
    "ARTIFACT_MANIFEST_FILENAME",
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "ARTIFACT_STATES",
    "FINAL_ARTIFACT_STATES",
    "ArtifactError",
    "ArtifactIntegrityError",
    "ArtifactIssue",
    "ArtifactManifestEntry",
    "ArtifactManifest",
    "ArtifactMetadata",
    "ArtifactRecord",
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactValidationError",
]
