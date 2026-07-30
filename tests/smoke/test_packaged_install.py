"""インストール済みwheelの最小契約を検証するテスト.

このテストは、CIのpackagingジョブが作成した一時virtualenvのPythonを
``YOMIAGE_SMOKE_PYTHON`` から受け取る。通常のpytest実行ではwheel用Pythonが
指定されないため、配布物のテストを誤ってソースcheckoutへ向けない。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _smoke_python() -> Path:
    """packagingジョブが用意したPython実行ファイルを返す."""
    configured = os.environ.get("YOMIAGE_SMOKE_PYTHON")
    if not configured:
        pytest.skip("YOMIAGE_SMOKE_PYTHON is not set; run the packaging job")

    interpreter = Path(configured)
    if not interpreter.is_file():
        pytest.fail(f"wheel smoke interpreter does not exist: {interpreter}")
    return interpreter


def _run_installed_python(
    interpreter: Path, code: str, cwd: Path
) -> subprocess.CompletedProcess[str]:
    """リポジトリ外のCWDで、wheel側のPythonを隔離モードで実行する."""
    return subprocess.run(
        [str(interpreter), "-I", "-c", code],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.packaging
def test_wheel_imports_from_outside_checkout(tmp_path: Path) -> None:
    """wheel install後も、checkoutに依存せず ``import yomiage`` できる."""
    interpreter = _smoke_python()
    result = _run_installed_python(
        interpreter,
        "import yomiage; assert yomiage.__file__",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.packaging
def test_wheel_loads_packaged_default_config(tmp_path: Path) -> None:
    """wheel install後に、CWD非依存で標準設定を読み込める."""
    interpreter = _smoke_python()
    result = _run_installed_python(
        interpreter,
        (
            "from yomiage.api.config import PipelineConfig; "
            "from yomiage.config import load_config; "
            "from yomiage.resource_loader import packaged_config_resource; "
            "config = PipelineConfig.from_yaml(); "
            "assert config.scene_params and config.scene_params.get('scenes'); "
            "raw = load_config(); "
            "assert raw.get('math_dict', {}).get('unicode_symbols'); "
            "default = packaged_config_resource('default.yaml').read_text(encoding='utf-8'); "
            "assert '/home/' not in default and '/Users/' not in default; "
            "assert 'Voicepeak-linux64' not in default"
        ),
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


if __name__ == "__main__":  # pragma: no cover - convenience for local debugging
    raise SystemExit(pytest.main([__file__, "-m", "packaging", *sys.argv[1:]]))
