"""UtterancePlan と、その明示的な直列化境界."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from .migration import (
    PlanSerializer,
    SchemaMigrationRegistry,
)
from .models import PlanWarning, Utterance

CURRENT_SCHEMA_VERSION = 1

# DOM-003 keeps the registry explicit even while schema 1 is current.  A future schema must be
# registered here before readers can accept it; otherwise it is rejected instead of being silently
# truncated to the fields known by this version.
PLAN_SCHEMA_MIGRATIONS = SchemaMigrationRegistry(CURRENT_SCHEMA_VERSION)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _schema_version(value: object) -> int | str:
    """schema versionを検証する.

    初期契約では数値の ``1`` を使うが、将来のschema migrationで ``"1.1"`` のような
    表記を扱えるよう数値文字列も許可し、入力の型は保持する。
    """

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError("schema_version must be a positive integer or version string")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("schema_version must be positive")
        return value
    normalized = value.strip()
    if not normalized or any(part == "" for part in normalized.split(".")):
        raise ValueError("schema_version must not be empty")
    parts = normalized.split(".")
    if any(not part.isdigit() for part in parts) or int(parts[0]) <= 0:
        raise ValueError("schema_version must be a positive version")
    return normalized


def _created_at(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("created_at must not be empty")
        try:
            # ISO 8601の ``Z`` をPythonのUTC offsetへ変換する。
            result = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("created_at must be an ISO 8601 datetime") from exc
    else:
        raise TypeError("created_at must be a datetime or ISO 8601 string")
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _coerce_utterances(
    value: Sequence[Utterance] | Sequence[Mapping[str, Any]],
) -> tuple[Utterance, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError("utterances must be a sequence")
    result: list[Utterance] = []
    for item in value:
        if isinstance(item, Utterance):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(Utterance.from_dict(item))
        else:
            raise TypeError("utterances must contain Utterance or mapping values")
    return tuple(result)


def _coerce_warnings(
    value: Sequence[PlanWarning] | Sequence[Mapping[str, Any]],
) -> tuple[PlanWarning, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError("warnings must be a sequence")
    result: list[PlanWarning] = []
    for item in value:
        if isinstance(item, PlanWarning):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(PlanWarning.from_dict(item))
        else:
            raise TypeError("warnings must contain PlanWarning or mapping values")
    return tuple(result)


class PlanValidationError(ValueError):
    """UtterancePlanの構造・順序が不正な場合のエラー."""

@dataclass(frozen=True, slots=True)
class UtterancePlan:
    """解析結果と合成実行を分離する immutable な発話計画."""

    schema_version: int | str
    plan_id: str
    source_fingerprint: str
    config_fingerprint: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    utterances: tuple[Utterance, ...] = field(default_factory=tuple)
    warnings: tuple[PlanWarning, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "plan_id", _required_text(self.plan_id, "plan_id"))
        object.__setattr__(
            self,
            "source_fingerprint",
            _required_text(self.source_fingerprint, "source_fingerprint"),
        )
        object.__setattr__(
            self,
            "config_fingerprint",
            _required_text(self.config_fingerprint, "config_fingerprint"),
        )
        object.__setattr__(self, "created_at", _created_at(self.created_at))
        utterances = _coerce_utterances(self.utterances)
        warnings = _coerce_warnings(self.warnings)
        self._validate_utterances(utterances)
        object.__setattr__(self, "utterances", utterances)
        object.__setattr__(self, "warnings", warnings)

    @staticmethod
    def _validate_utterances(utterances: Sequence[Utterance]) -> None:
        ids: set[str] = set()
        orders: set[int] = set()
        previous_order: int | None = None
        for utterance in utterances:
            if utterance.id in ids:
                raise PlanValidationError(f"duplicate utterance id: {utterance.id}")
            if utterance.order in orders:
                raise PlanValidationError(f"duplicate utterance order: {utterance.order}")
            if previous_order is not None and utterance.order <= previous_order:
                raise PlanValidationError("utterances must be ordered by ascending order")
            ids.add(utterance.id)
            orders.add(utterance.order)
            previous_order = utterance.order

    @property
    def ordered_utterances(self) -> tuple[Utterance, ...]:
        """再生順を明示する読み取り専用alias."""

        return self.utterances

    def to_dict(self) -> dict[str, Any]:
        """JSONへ渡せる組み込み型だけの辞書へ変換する."""

        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "source_fingerprint": self.source_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "created_at": self.created_at.isoformat(),
            "utterances": [item.to_dict() for item in self.utterances],
            "warnings": [item.to_dict() for item in self.warnings],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UtterancePlan:
        """辞書からUtterancePlanを復元する.

        ``schema_version`` は将来のmigrationが黙って未知データを受理しないよう必須とする。
        migrationそのものはDOM-003で追加し、ここでは値の形式だけを検証する。
        """

        if not isinstance(data, Mapping):
            raise TypeError("plan must be a mapping")
        migrated = PLAN_SCHEMA_MIGRATIONS.migrate(data)
        required = (
            "schema_version",
            "plan_id",
            "source_fingerprint",
            "config_fingerprint",
            "created_at",
            "utterances",
            "warnings",
        )
        missing = [key for key in required if key not in migrated]
        if missing:
            raise ValueError(f"plan fields are required: {', '.join(missing)}")
        return cls(
            schema_version=migrated["schema_version"],
            plan_id=migrated["plan_id"],
            source_fingerprint=migrated["source_fingerprint"],
            config_fingerprint=migrated["config_fingerprint"],
            created_at=migrated["created_at"],
            utterances=migrated["utterances"],
            warnings=migrated["warnings"],
        )

    def to_json(self, *, indent: int | None = None) -> str:
        """canonicalなJSON文字列へ変換する."""

        return PlanSerializer.dumps(self, indent=indent)

    @classmethod
    def from_json(cls, value: str | bytes | bytearray) -> UtterancePlan:
        """JSON文字列からUtterancePlanを復元する."""

        loaded = PlanSerializer.loads(value)
        if not isinstance(loaded, cls):
            raise TypeError("plan JSON did not decode to UtterancePlan")
        return loaded

    def with_utterances(self, utterances: Sequence[Utterance]) -> UtterancePlan:
        """発話を置換した新しいPlanを返す（元のPlanは変更しない）."""

        return type(self)(
            schema_version=self.schema_version,
            plan_id=self.plan_id,
            source_fingerprint=self.source_fingerprint,
            config_fingerprint=self.config_fingerprint,
            created_at=self.created_at,
            utterances=utterances,
            warnings=self.warnings,
        )

    def with_warning(self, warning: PlanWarning | Mapping[str, Any]) -> UtterancePlan:
        """warningを追加した新しいPlanを返す."""

        item = warning if isinstance(warning, PlanWarning) else PlanWarning.from_dict(warning)
        return type(self)(
            schema_version=self.schema_version,
            plan_id=self.plan_id,
            source_fingerprint=self.source_fingerprint,
            config_fingerprint=self.config_fingerprint,
            created_at=self.created_at,
            utterances=self.utterances,
            warnings=(*self.warnings, item),
        )


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "PLAN_SCHEMA_MIGRATIONS",
    "PlanValidationError",
    "PlanSerializer",
    "UtterancePlan",
]
