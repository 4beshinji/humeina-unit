"""optional extra の有無を判定し、不足時に導入方法を案内する。"""

from __future__ import annotations

import importlib.util

__all__ = ["MissingExtraError", "extra_available", "require_extra"]


class MissingExtraError(RuntimeError):
    """optional extra の依存が未導入であることを表す。"""

    def __init__(self, extra: str, module: str) -> None:
        self.extra = extra
        self.module = module
        super().__init__(
            f"この機能には optional extra '{extra}' が必要です（{module} が見つかりません）。"
            f" 導入: uv sync --extra {extra}  または  pip install 'humeina-unit[{extra}]'"
        )


def extra_available(module: str) -> bool:
    """``module`` が import 可能なら True を返す。"""

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def require_extra(extra: str, *modules: str) -> None:
    """``modules`` のいずれかが無ければ :class:`MissingExtraError` を送出する。"""

    for module in modules:
        if not extra_available(module):
            raise MissingExtraError(extra, module)
