"""旧 NLP/Batch 結果を新しい planning 境界へ渡す互換 adapter.

旧 ``AnalysisResult``、``AnalyzedSegment``、``SentenceEntry`` はそれぞれ異なる型を持つ
ため、入口側が直接 ``Utterance`` を組み立てないよう変換を一か所へ寄せる。実装本体は
application 層の ``UtterancePlanner`` であり、この module は旧 import 経路の薄い facade
だけを提供する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..application.planning import (
    BatchPlanningAdapter,
    UtterancePlanner,
    plan_from_results,
)

if TYPE_CHECKING:
    from ..batch.manifest import BatchManifest
    from ..core.plan import UtterancePlan


class LegacyNLPAdapter:
    """旧 NLP 結果を ``UtterancePlan`` へ変換する facade."""

    def __init__(self, planner: UtterancePlanner | None = None):
        self.planner = planner or UtterancePlanner()

    def to_plan(self, results: object, **kwargs: Any) -> "UtterancePlan":
        return self.planner.build(results, **kwargs)

    from_results = to_plan

    def from_batch_manifest(
        self, manifest: "BatchManifest", **kwargs: Any
    ) -> "UtterancePlan":
        return BatchPlanningAdapter(self.planner).plan(manifest, **kwargs)


def analysis_to_plan(results: object, **kwargs: Any) -> "UtterancePlan":
    """旧 API/Scene 分析結果を plan 化する便利関数."""

    return plan_from_results(results, **kwargs)


def batch_manifest_to_plan(
    manifest: "BatchManifest", **kwargs: Any
) -> "UtterancePlan":
    """旧 Batch の2-pass manifestを plan 化する便利関数."""

    return BatchPlanningAdapter().plan(manifest, **kwargs)


__all__ = [
    "LegacyNLPAdapter",
    "analysis_to_plan",
    "batch_manifest_to_plan",
]
