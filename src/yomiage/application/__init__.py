"""ユースケースとアプリケーションサービスの境界。

FastAPI、Typer、具象TTS Providerはこのパッケージから直接参照しない。
composition rootでportとadapterを組み立て、後続タスクでユースケースを追加する。
"""

from .jobs import (
    JobAlreadyActiveError,
    JobService,
    SupervisorClosedError,
    TaskOperation,
    TaskQueueFullError,
    TaskSupervisor,
    TaskSupervisorError,
)
from .planning import (
    ApiPlanningAdapter,
    BatchPlanningAdapter,
    NLPPlanAdapter,
    PlanningContext,
    PlanningService,
    ReaderPlanningAdapter,
    UtterancePlanBuilder,
    UtterancePlanner,
    build_plan,
    plan_from_batch_manifest,
    plan_from_nlp,
    plan_from_results,
    plan_text,
)
from .synthesis import (
    RetryPolicy,
    SynthesisFailure,
    SynthesisRequest,
    SynthesisResult,
    SynthesisService,
)

__all__ = [
    "ApiPlanningAdapter",
    "BatchPlanningAdapter",
    "NLPPlanAdapter",
    "PlanningContext",
    "PlanningService",
    "ReaderPlanningAdapter",
    "UtterancePlanBuilder",
    "UtterancePlanner",
    "build_plan",
    "plan_from_batch_manifest",
    "plan_from_nlp",
    "plan_from_results",
    "plan_text",
    "JobAlreadyActiveError",
    "JobService",
    "SupervisorClosedError",
    "TaskOperation",
    "TaskQueueFullError",
    "TaskSupervisor",
    "TaskSupervisorError",
    "RetryPolicy",
    "SynthesisFailure",
    "SynthesisRequest",
    "SynthesisResult",
    "SynthesisService",
]
