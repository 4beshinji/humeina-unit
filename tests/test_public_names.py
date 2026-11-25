"""公開 package 間で同名・別実体の型を export しないことの検査."""

from __future__ import annotations

import importlib
from types import ModuleType, SimpleNamespace

PUBLIC_PACKAGES = ("yomiage", "yomiage.api", "yomiage.application", "yomiage.core")


def _name_collisions(modules: dict[str, ModuleType | SimpleNamespace]) -> dict[str, set[str]]:
    """``__all__`` 上で同じ名前が別の class を指すものを返す."""

    seen: dict[str, dict[int, str]] = {}
    for module_name, module in modules.items():
        for name in getattr(module, "__all__", ()):
            value = getattr(module, name)
            if not isinstance(value, type):
                continue
            seen.setdefault(name, {})[id(value)] = f"{value.__module__}.{value.__qualname__}"
    return {name: set(owners.values()) for name, owners in seen.items() if len(owners) > 1}


def test_public_packages_do_not_export_distinct_types_with_same_name():
    modules = {name: importlib.import_module(name) for name in PUBLIC_PACKAGES}
    assert _name_collisions(modules) == {}


def test_name_collision_is_detected():
    first = type("SynthesisResult", (), {})
    second = type("SynthesisResult", (), {})
    modules = {
        "a": SimpleNamespace(__all__=["SynthesisResult"], SynthesisResult=first),
        "b": SimpleNamespace(__all__=["SynthesisResult"], SynthesisResult=second),
    }
    assert set(_name_collisions(modules)) == {"SynthesisResult"}


def test_segment_type_has_single_definition():
    from yomiage.core.models import SegmentType as CoreSegmentType
    from yomiage.nlp.classifier import SegmentType as NLPSegmentType

    assert NLPSegmentType is CoreSegmentType


def test_top_level_synthesis_result_is_public_sdk_type():
    import yomiage
    from yomiage.api.models import SynthesisResult

    assert yomiage.SynthesisResult is SynthesisResult
