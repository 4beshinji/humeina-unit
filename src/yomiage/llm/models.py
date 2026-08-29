"""LLMリクエスト、レスポンス、エラーの共通モデル。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class LLMErrorCategory(StrEnum):
    """ログやfallback判定に使う有限のエラー分類。"""

    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_LENGTH = "context_length"
    SERVER = "server"
    INVALID_RESPONSE = "invalid_response"
    BUDGET = "budget"
    UNKNOWN = "unknown"


class LLMError(RuntimeError):
    """Provider固有の本文を外へ漏らさない正規化済みエラー。"""

    def __init__(
        self,
        category: LLMErrorCategory,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class LLMUsage:
    """API形式をまたいで正規化したtoken利用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMRequest:
    """一回の論理LLM生成要求。"""

    task: str
    prompt: str
    system: str | None = None
    response_mode: Literal["text", "json"] = "text"
    json_schema: dict[str, Any] | None = None
    temperature: float = 0.3
    max_output_tokens: int = 2048
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class LLMResponse:
    """一回の物理Provider応答。"""

    text: str
    provider: str
    model: str
    protocol: str
    usage: LLMUsage = LLMUsage()
    latency_ms: float = 0.0
    retry_count: int = 0
