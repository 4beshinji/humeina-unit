"""schema version の明示的な移行境界。

Plan は immutable な値として保存するため、読み込み時に「たまたま似ている」将来の
JSONを受理してはいけない。このモジュールは、既知の schema 間の変換だけを登録して
適用する小さな registry を提供する。filesystem や Batch の型はここへ持ち込まず、
Plan serializer もこの registry を通る。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias


class SchemaMigrationError(ValueError):
    """schema を安全に移行できない場合の基底例外。"""


class UnknownSchemaVersionError(SchemaMigrationError):
    """未登録または現在より新しい schema version。"""


class MigrationPathError(SchemaMigrationError):
    """登録済み migration だけでは目的 version に到達できない。"""


SchemaVersion: TypeAlias = int | str
MigrationFunction: TypeAlias = Callable[[dict[str, Any]], Mapping[str, Any]]


def _version_key(value: object) -> tuple[int, ...]:
    """version を比較可能な tuple にする。

    Plan の wire format は ``1`` と ``"1"`` のどちらも読み取り可能だが、空文字、
    小数、負数などは schema version として受理しない。``"1.0"`` は ``(1, 0)`` と
    して扱い、registry で明示登録された場合だけ移行できる。
    """

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise UnknownSchemaVersionError("schema_version must be a positive integer or version")
    if isinstance(value, int):
        if value <= 0:
            raise UnknownSchemaVersionError(f"unsupported schema_version: {value}")
        return (value,)
    normalized = value.strip()
    parts = normalized.split(".") if normalized else []
    if not parts or any(not part.isdigit() for part in parts):
        raise UnknownSchemaVersionError(f"unsupported schema_version: {value!r}")
    result = tuple(int(part) for part in parts)
    if result[0] <= 0:
        raise UnknownSchemaVersionError(f"unsupported schema_version: {value!r}")
    return result


def normalize_schema_version(value: object) -> int | str:
    """schema version の入力型を検証し、整数表記は整数へ正規化する。"""

    key = _version_key(value)
    if len(key) == 1:
        return key[0]
    # Keep dotted versions stable, without accepting redundant leading zeroes as a new value.
    return ".".join(str(part) for part in key)


def _version_value(key: tuple[int, ...]) -> int | str:
    """内部の比較tupleをwire formatへ戻す。"""

    return key[0] if len(key) == 1 else ".".join(str(part) for part in key)


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """一つの schema migration の説明。"""

    source_version: tuple[int, ...]
    target_version: tuple[int, ...]
    function: MigrationFunction


class SchemaMigrationRegistry:
    """明示登録された schema migration の有向グラフ。

    migration は副作用を持たない関数として扱い、入力 mapping をコピーして渡す。同じ
    registry は plan と manifest の両方で利用できるが、各ドメインは別の current version
    と登録表を持つ。登録 API は decorator と通常呼び出しの両方に対応する。
    """

    def __init__(self, current_version: SchemaVersion):
        self.current_version = normalize_schema_version(current_version)
        self._steps: dict[tuple[tuple[int, ...], tuple[int, ...]], MigrationStep] = {}

    def register(
        self,
        source_version: SchemaVersion,
        target_version: SchemaVersion,
        function: MigrationFunction | None = None,
    ) -> MigrationFunction | Callable[[MigrationFunction], MigrationFunction]:
        """migration を登録する（``@registry.register(1, 2)`` も可）。"""

        source = _version_key(source_version)
        target = _version_key(target_version)
        if source == target:
            raise ValueError("migration source and target must differ")

        def _store(migration: MigrationFunction) -> MigrationFunction:
            if not callable(migration):
                raise TypeError("migration must be callable")
            key = (source, target)
            if key in self._steps:
                raise ValueError(
                    f"migration already registered: {normalize_schema_version(source_version)}"
                    f" -> {normalize_schema_version(target_version)}"
                )
            self._steps[key] = MigrationStep(source, target, migration)
            return migration

        if function is None:
            return _store
        return _store(function)

    def steps(self) -> tuple[MigrationStep, ...]:
        """登録済み migration を読み取り専用 tuple で返す。"""

        return tuple(self._steps.values())

    def _path(
        self,
        source: tuple[int, ...],
        target: tuple[int, ...],
    ) -> tuple[MigrationStep, ...]:
        if source == target:
            return ()
        # Breadth-first search keeps multi-hop migrations deterministic and avoids requiring each
        # task to duplicate a direct edge from every historical version to current.
        queue: list[tuple[tuple[int, ...], tuple[MigrationStep, ...]]] = [(source, ())]
        visited = {source}
        while queue:
            version, path = queue.pop(0)
            outgoing = sorted(
                (step for step in self._steps.values() if step.source_version == version),
                key=lambda step: step.target_version,
            )
            for step in outgoing:
                if step.target_version in visited:
                    continue
                next_path = (*path, step)
                if step.target_version == target:
                    return next_path
                visited.add(step.target_version)
                queue.append((step.target_version, next_path))
        raise MigrationPathError(
            f"no migration path from {_version_value(source)} "
            f"to {_version_value(target)}"
        )

    def migrate(
        self,
        payload: Mapping[str, Any],
        *,
        target_version: SchemaVersion | None = None,
    ) -> dict[str, Any]:
        """payload を target version へ移行する。

        未知の future version は、既知フィールドだけを取り出して続行せず明示的に失敗
        する。入力・各 migration の戻り値は mapping でなければならない。
        """

        if not isinstance(payload, Mapping):
            raise TypeError("schema payload must be a mapping")
        if "schema_version" not in payload:
            raise UnknownSchemaVersionError("schema_version is required")
        source_value = normalize_schema_version(payload["schema_version"])
        source = _version_key(source_value)
        resolved_target = normalize_schema_version(
            self.current_version if target_version is None else target_version
        )
        target = _version_key(resolved_target)
        if source > target:
            raise UnknownSchemaVersionError(
                f"unsupported future schema_version: {source_value} (current {resolved_target})"
            )
        result = dict(payload)
        for step in self._path(source, target):
            try:
                migrated = step.function(dict(result))
            except SchemaMigrationError:
                raise
            except Exception as exc:  # pragma: no cover - defensive wrapping for plugin steps
                raise SchemaMigrationError(
                    f"migration failed: {_version_value(step.source_version)} -> "
                    f"{_version_value(step.target_version)}"
                ) from exc
            if not isinstance(migrated, Mapping):
                raise SchemaMigrationError("migration must return a mapping")
            result = dict(migrated)
            result["schema_version"] = _version_value(step.target_version)
        result["schema_version"] = _version_value(target)
        return result


class PlanSerializer:
    """UtterancePlan の canonical JSON serializer/loader。

    文字列の読み込みは必ず ``UtterancePlan.from_dict`` を通るため、future schema の拒否と
    登録済み migration の適用が同じ境界で行われる。Path IO は持たせず、保存先のatomic
    方針は上位のadapterに委ねる。
    """

    @staticmethod
    def dumps(plan: object, *, indent: int | None = None) -> str:
        to_dict = getattr(plan, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("plan must provide to_dict()")
        return json.dumps(
            to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            separators=None if indent is not None else (",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def loads(value: str | bytes | bytearray) -> object:
        if not isinstance(value, (str, bytes, bytearray)):
            raise TypeError("plan JSON must be a string or bytes")
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid plan JSON") from exc
        if not isinstance(data, Mapping):
            raise TypeError("plan JSON must decode to a mapping")
        # Local import avoids a module cycle while keeping migration.py provider independent.
        from .plan import UtterancePlan

        return UtterancePlan.from_dict(data)


__all__ = [
    "MigrationFunction",
    "MigrationPathError",
    "MigrationStep",
    "PlanSerializer",
    "SchemaMigrationError",
    "SchemaMigrationRegistry",
    "SchemaVersion",
    "UnknownSchemaVersionError",
    "normalize_schema_version",
]
