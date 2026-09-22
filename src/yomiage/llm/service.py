"""LLM routing、retry、fallback、cache、利用量管理。"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator, Iterable

from loguru import logger

from ..core.text import STATIC_READINGS as _STATIC_READINGS
from ..nlp.llm_utils import extract_json
from .budgeting import estimate_tokens
from .cache import LLMCache
from .models import LLMError, LLMErrorCategory, LLMRequest, LLMResponse
from .protocols import LLMBackend, LLMTransport

_RETRYABLE = {
    LLMErrorCategory.UNAVAILABLE,
    LLMErrorCategory.TIMEOUT,
    LLMErrorCategory.RATE_LIMIT,
    LLMErrorCategory.SERVER,
}
_ROMAN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9.+#_/-]*")


@dataclass(slots=True)
class _JobBudget:
    job_id: str
    requests: int = 0
    estimated_input_tokens: int = 0


class LLMService(LLMBackend):
    """Featureから具象Providerを隠す共有実行境界。"""

    def __init__(
        self,
        transports: dict[str, LLMTransport],
        *,
        routes: dict[str, dict[str, Any]] | None = None,
        fallback_order: Iterable[str] = (),
        fallback_on: Iterable[str] = ("unavailable", "timeout", "rate_limit"),
        max_retries: int = 2,
        availability_ttl_seconds: float = 60.0,
        circuit_failure_threshold: int = 3,
        max_requests_per_job: int = 100,
        max_input_tokens_per_job: int = 500_000,
        metrics: Any | None = None,
        cache: LLMCache | None = None,
    ) -> None:
        if not transports:
            raise ValueError("at least one LLM transport is required")
        self.transports = transports
        self.routes = routes or {}
        self.fallback_order = tuple(fallback_order)
        self.fallback_on = frozenset(fallback_on)
        self.max_retries = max(0, max_retries)
        self.availability_ttl_seconds = max(0.0, availability_ttl_seconds)
        self.circuit_failure_threshold = max(1, circuit_failure_threshold)
        self.max_requests_per_job = max(1, max_requests_per_job)
        self.max_input_tokens_per_job = max(1, max_input_tokens_per_job)
        self.metrics = metrics
        self.cache = cache or LLMCache()
        self._health: dict[str, tuple[float, bool]] = {}
        self._failures: dict[str, int] = {}
        self._circuit_until: dict[str, float] = {}
        self._requests = 0
        self._estimated_input_tokens = 0
        self._job_budget: contextvars.ContextVar[_JobBudget | None] = contextvars.ContextVar(
            f"llm_job_budget_{id(self)}", default=None
        )
        self._closed = False

    def _providers_for(self, task: str) -> list[str]:
        route = self.routes.get(task) or self.routes.get("default") or {}
        primary = route.get("provider")
        result: list[str] = []
        if isinstance(primary, str):
            result.append(primary)
        elif self.transports:
            result.append(next(iter(self.transports)))
        for provider in self.fallback_order:
            if provider not in result:
                result.append(provider)
        return [provider for provider in result if provider in self.transports]

    def analysis_identity(self) -> dict:
        """Planがモデル・ルーティングの変更を追跡するための、認証情報を含まない情報。"""
        return {
            "routes": self.routes,
            "models": {
                name: {
                    "model": getattr(transport, "model", None),
                    "protocol": type(transport).__name__,
                }
                for name, transport in self.transports.items()
            },
        }

    @asynccontextmanager
    async def ensure_job_scope(self, job_id: str) -> AsyncIterator[None]:
        """外側のjob予算を保持し、SDK/Readerの独立呼び出しだけ新しい予算を作る。"""
        if self._job_budget.get() is not None:
            yield
        else:
            async with self.job_scope(job_id):
                yield

    def effective_output_limit(self, task: str, requested: int = 4096) -> int:
        """窓の計画と物理要求に共通の出力上限を返す。"""
        route = self.routes.get(task) or self.routes.get("default") or {}
        limit = route.get("max_output_tokens", requested)
        return min(requested, limit) if type(limit) is int else requested

    def _route_request(self, request: LLMRequest) -> LLMRequest:
        route = self.routes.get(request.task) or self.routes.get("default") or {}
        limit = route.get("max_output_tokens")
        if isinstance(limit, int):
            return replace(request, max_output_tokens=min(request.max_output_tokens, limit))
        return request

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """設定されたtask routeをretry/fallback付きで実行する。"""

        if self._closed:
            raise RuntimeError("LLMService is closed")
        request = self._route_request(request)
        estimated = estimate_tokens((request.system or "") + request.prompt)

        cache_key = self.cache.key(
            "completion",
            {
                "task": request.task,
                "system": request.system,
                "prompt": request.prompt,
                "mode": request.response_mode,
                "schema": request.json_schema,
                "temperature": request.temperature,
                "max": request.max_output_tokens,
            },
        )
        cached = self.cache.get(cache_key)
        if isinstance(cached, LLMResponse):
            self._record_cache(request.task, cached, hit=True)
            return cached

        providers = self._providers_for(request.task)
        if not providers:
            raise LLMError(LLMErrorCategory.UNAVAILABLE, "no LLM provider for task")
        last_error: LLMError | None = None
        for provider_index, provider in enumerate(providers):
            transport = self.transports[provider]
            if self._circuit_until.get(provider, 0.0) > time.monotonic():
                last_error = LLMError(LLMErrorCategory.UNAVAILABLE, f"{provider} circuit is open")
                continue
            for retry in range(self.max_retries + 1):
                self._consume_budget(estimated)
                try:
                    response = await transport.complete(request)
                except LLMError as exc:
                    last_error = exc
                    self._record_error(request.task, transport, exc, retry)
                    self._mark_failure(provider)
                    if exc.category not in _RETRYABLE or retry >= self.max_retries:
                        break
                    delay = exc.retry_after
                    if delay is None:
                        delay = min(2.0, 0.1 * (2**retry))
                    await asyncio.sleep(delay)
                    continue
                except Exception as exc:
                    last_error = LLMError(LLMErrorCategory.UNKNOWN, f"{provider} generation failed")
                    self._record_error(request.task, transport, last_error, retry)
                    self._mark_failure(provider)
                    logger.debug(f"Unexpected LLM transport error: {type(exc).__name__}")
                    break
                self._mark_success(provider)
                response = replace(response, retry_count=retry)
                self.cache.put(cache_key, response)
                self._record_success(
                    request.task,
                    response,
                    retry_count=retry,
                    fallback=provider_index > 0,
                )
                return response
            if last_error is None or last_error.category.value not in self.fallback_on:
                break
        raise last_error or LLMError(LLMErrorCategory.UNKNOWN, "LLM generation failed")

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        task: str = "generic",
    ) -> str:
        response = await self.complete(
            LLMRequest(
                task=task,
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
        )
        return response.text

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
        task: str = "generic",
        json_schema: dict | None = None,
        max_tokens: int = 2048,
    ) -> list | dict:
        response = await self.complete(
            LLMRequest(
                task=task,
                prompt=prompt,
                system=system,
                response_mode="json",
                json_schema=json_schema,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
        )
        parsed = extract_json(response.text)
        if parsed == {} and response.text.strip() not in {"{}", "```json\n{}\n```"}:
            self._record_validation_failure(task, response)
            raise LLMError(LLMErrorCategory.INVALID_RESPONSE, "LLM returned invalid JSON")
        if json_schema and not _matches_schema(parsed, json_schema):
            self._record_validation_failure(task, response)
            raise LLMError(
                LLMErrorCategory.INVALID_RESPONSE,
                "LLM JSON does not match the requested schema",
            )
        return parsed

    async def romanize_many(self, texts: list[str]) -> list[str]:
        """未知英字語句だけを重複排除し、一回の構造化要求で変換する。"""

        if not texts:
            return []
        terms = list(dict.fromkeys(term for text in texts for term in _ROMAN_TOKEN.findall(text)))
        readings = dict(_STATIC_READINGS)
        unknown: list[str] = []
        for term in terms:
            cached = self.cache.get(self.cache.key("romanization", term))
            if isinstance(cached, str):
                readings[term] = cached
            elif term not in readings:
                unknown.append(term)
        if unknown:
            items = [{"id": index, "term": term} for index, term in enumerate(unknown)]
            prompt = (
                "次の英字語句を日本語音声読み上げ用のカタカナへ変換してください。"
                "固有名詞は一般的な日本語読みを使い、JSON配列のみ返してください。\n"
                + json.dumps(items, ensure_ascii=False)
                + '\n形式: [{"id": 0, "reading": "..."}]'
            )
            try:
                result = await self.generate_json(
                    prompt,
                    system="あなたは日本語の読み仮名変換器です。",
                    temperature=0.1,
                    task="romanization",
                    json_schema={
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "reading": {"type": "string"},
                            },
                            "required": ["id", "reading"],
                        },
                    },
                    max_tokens=max(128, len(unknown) * 24),
                )
            except Exception as exc:
                logger.warning(f"Romanization batch failed: {exc}")
                result = []
            if isinstance(result, list):
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    index = item.get("id")
                    reading = item.get("reading")
                    if (
                        isinstance(index, int)
                        and 0 <= index < len(unknown)
                        and isinstance(reading, str)
                    ):
                        term = unknown[index]
                        readings[term] = reading
                        self.cache.put(self.cache.key("romanization", term), reading)
        # 長い語句を先に置換し、部分一致による破損を避ける。
        ordered = sorted(readings.items(), key=lambda item: len(item[0]), reverse=True)
        result_texts: list[str] = []
        for text in texts:
            for term, reading in ordered:
                text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", reading, text)
            result_texts.append(text)
        return result_texts

    async def romanize(self, text: str) -> str:
        return (await self.romanize_many([text]))[0]

    async def is_available(self) -> bool:
        """Provider healthをTTL cacheし、feature側のprobe乱発を防ぐ。"""

        now = time.monotonic()
        for provider in self._providers_for("default"):
            cached = self._health.get(provider)
            if cached and now - cached[0] < self.availability_ttl_seconds:
                if cached[1]:
                    return True
                continue
            available = await self.transports[provider].health()
            self._health[provider] = (now, available)
            if available:
                return True
        return False

    def reset_job_budget(self) -> None:
        budget = self._job_budget.get()
        if budget is None:
            self._requests = 0
            self._estimated_input_tokens = 0
        else:
            budget.requests = 0
            budget.estimated_input_tokens = 0

    @asynccontextmanager
    async def job_scope(self, job_id: str) -> AsyncIterator[None]:
        """concurrent jobへ独立した物理request/token budgetを割り当てる。"""

        resolved = str(job_id).strip()
        if not resolved:
            raise ValueError("job_id must not be empty")
        token = self._job_budget.set(_JobBudget(resolved))
        try:
            yield
        finally:
            self._job_budget.reset(token)

    def _consume_budget(self, estimated_tokens: int) -> None:
        budget = self._job_budget.get()
        requests = budget.requests if budget is not None else self._requests
        tokens = (
            budget.estimated_input_tokens if budget is not None else self._estimated_input_tokens
        )
        if requests >= self.max_requests_per_job:
            raise LLMError(LLMErrorCategory.BUDGET, "LLM request budget exceeded")
        if tokens + estimated_tokens > self.max_input_tokens_per_job:
            raise LLMError(LLMErrorCategory.BUDGET, "LLM input token budget exceeded")
        if budget is not None:
            budget.requests += 1
            budget.estimated_input_tokens += estimated_tokens
        else:
            self._requests += 1
            self._estimated_input_tokens += estimated_tokens

    def _mark_failure(self, provider: str) -> None:
        failures = self._failures.get(provider, 0) + 1
        self._failures[provider] = failures
        if failures >= self.circuit_failure_threshold:
            self._circuit_until[provider] = time.monotonic() + self.availability_ttl_seconds

    def _mark_success(self, provider: str) -> None:
        self._failures[provider] = 0
        self._circuit_until.pop(provider, None)
        self._health[provider] = (time.monotonic(), True)

    def _record_success(
        self, task: str, response: LLMResponse, *, retry_count: int, fallback: bool
    ) -> None:
        if self.metrics and hasattr(self.metrics, "record_llm"):
            self.metrics.record_llm(
                task=task,
                provider=response.provider,
                model=response.model,
                protocol=response.protocol,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cached_input_tokens=response.usage.cached_input_tokens,
                latency_ms=response.latency_ms,
                retry_count=retry_count,
                fallback=fallback,
            )

    def _record_error(
        self, task: str, transport: LLMTransport, error: LLMError, retry_count: int
    ) -> None:
        if self.metrics and hasattr(self.metrics, "record_llm"):
            self.metrics.record_llm(
                task=task,
                provider=transport.provider,
                model=transport.model,
                protocol=transport.protocol.value,
                error_category=error.category.value,
                retry_count=retry_count,
            )

    def _record_cache(self, task: str, response: LLMResponse, *, hit: bool) -> None:
        if self.metrics and hasattr(self.metrics, "record_llm_cache"):
            self.metrics.record_llm_cache(
                task=task,
                provider=response.provider,
                model=response.model,
                protocol=response.protocol,
                hit=hit,
            )

    def _record_validation_failure(self, task: str, response: LLMResponse) -> None:
        if self.metrics and hasattr(self.metrics, "record_llm_validation_failure"):
            self.metrics.record_llm_validation_failure(
                task=task,
                provider=response.provider,
                model=response.model,
                protocol=response.protocol,
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        for transport in self.transports.values():
            if id(transport) in seen:
                continue
            seen.add(id(transport))
            await transport.close()

    async def __aenter__(self) -> LLMService:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def _matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    """外部依存なしで生成境界に必要なJSON Schema部分集合を検証する。"""

    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if isinstance(expected, str) and expected in type_checks and not type_checks[expected](value):
        return False
    if isinstance(expected, list) and not any(
        kind in type_checks and type_checks[kind](value) for kind in expected
    ):
        return False
    if isinstance(value, dict):
        required = schema.get("required") or []
        if any(key not in value for key in required):
            return False
        properties = schema.get("properties") or {}
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                if not _matches_schema(value[key], child_schema):
                    return False
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return all(_matches_schema(item, schema["items"]) for item in value)
    return True
