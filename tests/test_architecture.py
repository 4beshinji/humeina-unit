"""新規内側レイヤーの依存方向をASTで検査する契約テスト."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "yomiage"
INNER_LAYER_NAMES = ("core", "application", "ports")

# application/core/portsから外側へ向かう依存を禁止する。tts.baseのような抽象契約は許可し、
# 実装Providerだけを列挙することで、移行途中の既存コードを過剰に拘束しない。
FORBIDDEN_IMPORT_PREFIXES = (
    "fastapi",
    "typer",
    "yomiage.tts.voicepeak",
    "yomiage.tts.voicevox",
    "yomiage.tts.voisona",
)


@dataclass(frozen=True)
class LegacyImportRule:
    """移行前の既存依存を一時的に許容する規則."""

    relative_path: str
    module_prefix: str
    owner_task: str


@dataclass(frozen=True)
class ImportViolation:
    """禁止依存のAST検出結果."""

    path: Path
    module: str
    lineno: int

    def format(self, repository_root: Path) -> str:
        relative = self.path.relative_to(repository_root).as_posix()
        return f"{relative}:{self.lineno}: forbidden import {self.module}"


# これらは既存の外側/旧経路に残る依存であり、対応する移行タスクを必ず明記する。
# 新規内側レイヤーの例外を追加する場合も、所有タスクなしでは受け入れない。
LEGACY_IMPORT_ALLOWLIST = (
    LegacyImportRule("src/yomiage/cli.py", "typer", "CLN-002"),
    LegacyImportRule("src/yomiage/cli.py", "yomiage.tts.voicepeak", "CLN-002"),
    LegacyImportRule("src/yomiage/cli.py", "yomiage.tts.voicevox", "CLN-002"),
    LegacyImportRule("src/yomiage/cli.py", "yomiage.tts.voisona", "CLN-002"),
    LegacyImportRule("src/yomiage/server.py", "fastapi", "API-001"),
    LegacyImportRule("src/yomiage/studio/engine.py", "typer", "SYN-002"),
    LegacyImportRule("src/yomiage/studio/engine.py", "yomiage.tts.voicepeak", "SYN-002"),
    LegacyImportRule("src/yomiage/studio/engine.py", "yomiage.tts.voicevox", "SYN-002"),
    LegacyImportRule("src/yomiage/studio/engine.py", "yomiage.tts.voisona", "SYN-002"),
    LegacyImportRule("src/yomiage/tools/tuner.py", "yomiage.tts.voisona", "CLN-001"),
    LegacyImportRule("src/yomiage/tts/factory.py", "yomiage.tts.voicepeak", "SYN-001"),
    LegacyImportRule("src/yomiage/tts/factory.py", "yomiage.tts.voicevox", "SYN-001"),
    LegacyImportRule("src/yomiage/tts/factory.py", "yomiage.tts.voisona", "SYN-001"),
)


def _module_context(path: Path, source_root: Path) -> tuple[str, tuple[str, ...]]:
    """ファイルパスからmodule名とpackage部分を作る."""

    relative_parts = list(path.relative_to(source_root).with_suffix("").parts)
    package_prefix = (source_root.name,)
    if relative_parts[-1] == "__init__":
        module_parts = package_prefix + tuple(relative_parts[:-1])
        package_parts = module_parts
    else:
        module_parts = package_prefix + tuple(relative_parts)
        package_parts = module_parts[:-1]
    return ".".join(module_parts), package_parts


def _resolve_import_from(
    node: ast.ImportFrom,
    package_parts: tuple[str, ...],
) -> str:
    """ImportFromのrelative levelを絶対module名へ解決する."""

    if node.level == 0:
        return node.module or ""
    parent_count = node.level - 1
    if parent_count > len(package_parts):
        return node.module or ""
    base_parts = package_parts[: len(package_parts) - parent_count]
    module_parts = tuple(node.module.split(".")) if node.module else ()
    return ".".join(base_parts + module_parts)


def _iter_imported_modules(
    node: ast.AST,
    package_parts: tuple[str, ...],
) -> tuple[str, ...]:
    """ASTノードが参照するmodule名を返す."""

    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if not isinstance(node, ast.ImportFrom):
        return ()

    module = _resolve_import_from(node, package_parts)
    if not module:
        return ()
    # `from yomiage.tts import voicevox` のような形式もProvider依存として検出する。
    if module == "yomiage.tts":
        aliases = tuple(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
        return (module, *aliases)
    return (module,)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _python_files(source_root: Path, layers: tuple[str, ...] | None) -> list[Path]:
    if layers is None:
        return sorted(source_root.rglob("*.py"))
    files: list[Path] = []
    for layer in layers:
        layer_root = source_root / layer
        if layer_root.is_dir():
            files.extend(layer_root.rglob("*.py"))
    return sorted(files)


def find_forbidden_imports(
    source_root: Path = SOURCE_ROOT,
    *,
    layers: tuple[str, ...] | None = INNER_LAYER_NAMES,
) -> tuple[ImportViolation, ...]:
    """指定レイヤーの禁止依存をimport実行なしで収集する."""

    source_root = Path(source_root)
    violations: list[ImportViolation] = []
    for path in _python_files(source_root, layers):
        module_name, package_parts = _module_context(path, source_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - source tree自体の破損を明示する
            raise AssertionError(f"cannot parse {path}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for module in _iter_imported_modules(node, package_parts):
                if _is_forbidden(module):
                    violations.append(ImportViolation(path, module, node.lineno))
    return tuple(violations)


def _legacy_rule_for(violation: ImportViolation) -> LegacyImportRule | None:
    relative = violation.path.relative_to(REPOSITORY_ROOT).as_posix()
    for rule in LEGACY_IMPORT_ALLOWLIST:
        if relative == rule.relative_path and (
            violation.module == rule.module_prefix
            or violation.module.startswith(f"{rule.module_prefix}.")
        ):
            return rule
    return None


def test_inner_layers_do_not_import_frameworks_or_concrete_providers():
    """core/application/portsは外側のframework/providerを参照しない."""

    violations = find_forbidden_imports()
    assert not violations, "\n".join(v.format(REPOSITORY_ROOT) for v in violations)


def test_forbidden_import_is_detected_for_an_inner_layer(tmp_path: Path):
    """禁止依存を追加した場合に、テストが失敗可能であることを確認する."""

    source_root = tmp_path / "yomiage"
    application_root = source_root / "application"
    application_root.mkdir(parents=True)
    (application_root / "bad.py").write_text(
        "from fastapi import FastAPI\nfrom yomiage.tts.voicevox import VoicevoxProvider\n",
        encoding="utf-8",
    )

    violations = find_forbidden_imports(source_root, layers=("application",))
    assert {violation.module for violation in violations} == {
        "fastapi",
        "yomiage.tts.voicevox",
    }


def test_existing_forbidden_imports_are_explicitly_owned():
    """既存経路の例外は所有タスクIDなしで許容しない."""

    violations = find_forbidden_imports(SOURCE_ROOT, layers=None)
    unowned = [violation for violation in violations if _legacy_rule_for(violation) is None]
    assert not unowned, "\n".join(v.format(REPOSITORY_ROOT) for v in unowned)


def test_allowlist_entries_have_task_ids_and_point_to_existing_files():
    """許容リストの追加が無主化・typo化しないことを確認する."""

    assert LEGACY_IMPORT_ALLOWLIST
    task_id_pattern = re.compile(r"^[A-Z]{3}-\d{3}$")
    for rule in LEGACY_IMPORT_ALLOWLIST:
        assert task_id_pattern.fullmatch(rule.owner_task), rule
        assert (REPOSITORY_ROOT / rule.relative_path).is_file(), rule


def test_batch_synthesis_facades_use_bootstrap_provider_construction():
    """Batch mode facades must not reintroduce concrete-provider constructors."""

    for name in ("voicevox_synth.py", "voisona_synth.py", "voicepeak_synth.py"):
        source = (SOURCE_ROOT / "batch" / name).read_text(encoding="utf-8")
        assert "from ..tts.voice" not in source
        assert "Provider(" not in source
        assert "hasattr(" not in source


def test_batch_synthesis_facades_keep_empty_config_compatibility(monkeypatch):
    """Legacy direct constructors still obtain a provider through bootstrap."""

    from yomiage import bootstrap
    from yomiage.batch.voicepeak_synth import VoicepeakBatchSynthesizer
    from yomiage.batch.voicevox_synth import VoicevoxBatchSynthesizer
    from yomiage.batch.voisona_synth import VoisonaBatchSynthesizer

    sentinel = object()
    calls: list[str] = []

    def fake_provider(_config, provider: str):
        calls.append(provider)
        return sentinel

    monkeypatch.setattr(bootstrap, "create_tts_provider", fake_provider)
    facades = (
        VoicevoxBatchSynthesizer({}, object()),
        VoicepeakBatchSynthesizer({}, object()),
        VoisonaBatchSynthesizer({}, object()),
    )

    assert all(facade.provider is sentinel for facade in facades)
    assert calls == ["voicevox", "voicepeak", "voisona"]
