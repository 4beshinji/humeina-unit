"""LLM共通基盤の課金なしcontract test。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aiohttp import web

from yomiage.llm.budgeting import TokenBudget, estimate_tokens, split_by_token_budget
from yomiage.llm.catalog import protocol_for_model
from yomiage.llm.factory import normalize_llm_config
from yomiage.llm.models import (
    LLMError,
    LLMErrorCategory,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)
from yomiage.llm.protocols import LLMProtocol, LLMTransport
from yomiage.llm.service import LLMService
from yomiage.llm.transports import (
    ChatCompletionsTransport,
    MessagesTransport,
    ResponsesTransport,
)
from yomiage.slack.monitor import SlackMessage
from yomiage.slack.scorer import ImportanceScorer


@asynccontextmanager
async def _server(handler: Any, path: str) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
    calls: list[dict[str, Any]] = []

    async def wrapped(request: web.Request) -> web.StreamResponse:
        calls.append(
            {
                "path": request.path,
                "headers": dict(request.headers),
                "body": await request.json(),
            }
        )
        return await handler(request)

    app = web.Application()
    app.router.add_post(path, wrapped)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets
    port = sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1", calls
    finally:
        await runner.cleanup()


async def test_chat_completions_contract_normalizes_json_and_usage() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            }
        )

    async with _server(handler, "/v1/chat/completions") as (base_url, calls):
        transport = ChatCompletionsTransport(
            provider="local", model="Qwen3.8-27B", base_url=base_url
        )
        try:
            response = await transport.complete(
                LLMRequest(
                    task="scene_analysis",
                    prompt="分析",
                    system="system",
                    response_mode="json",
                    json_schema={"type": "object"},
                    max_output_tokens=64,
                )
            )
        finally:
            await transport.close()

    assert response.text == '{"ok":true}'
    assert response.usage == LLMUsage(12, 3, 4)
    assert calls[0]["path"] == "/v1/chat/completions"
    assert calls[0]["body"]["response_format"]["type"] == "json_schema"
    assert calls[0]["body"]["messages"][0] == {"role": "system", "content": "system"}


async def test_responses_contract_extracts_nested_text_and_usage() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "output": [{"content": [{"type": "output_text", "text": "done"}]}],
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 5},
                },
            }
        )

    async with _server(handler, "/v1/responses") as (base_url, calls):
        transport = ResponsesTransport(
            provider="go", model="gpt-5.6-luna", base_url=base_url, api_key="secret"
        )
        try:
            response = await transport.complete(LLMRequest(task="test", prompt="hello"))
        finally:
            await transport.close()

    assert response.text == "done"
    assert response.usage == LLMUsage(8, 2, 5)
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"


async def test_messages_contract_sets_anthropic_headers_and_usage() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "content": [{"type": "text", "text": "message"}],
                "usage": {
                    "input_tokens": 9,
                    "output_tokens": 4,
                    "cache_read_input_tokens": 2,
                },
            }
        )

    async with _server(handler, "/v1/messages") as (base_url, calls):
        transport = MessagesTransport(
            provider="go", model="qwen3.8-flash", base_url=base_url, api_key="secret"
        )
        try:
            response = await transport.complete(LLMRequest(task="test", prompt="hello"))
        finally:
            await transport.close()

    assert response.text == "message"
    assert response.usage == LLMUsage(9, 4, 2)
    assert calls[0]["headers"]["x-api-key"] == "secret"
    assert calls[0]["headers"]["anthropic-version"] == "2023-06-01"


async def test_http_error_is_classified_without_response_body_leak() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=429, text="private prompt data", headers={"Retry-After": "7"})

    async with _server(handler, "/v1/chat/completions") as (base_url, _):
        transport = ChatCompletionsTransport(
            provider="go", model="glm-5.3-flash", base_url=base_url
        )
        try:
            with pytest.raises(LLMError) as captured:
                await transport.complete(LLMRequest(task="test", prompt="hello"))
        finally:
            await transport.close()

    assert captured.value.category == LLMErrorCategory.RATE_LIMIT
    assert captured.value.retry_after == 7
    assert "private prompt" not in str(captured.value)


class _FakeTransport(LLMTransport):
    def __init__(
        self,
        provider: str,
        *,
        failures: int = 0,
        text: str = "ok",
        available: bool = True,
    ) -> None:
        self.provider = provider
        self.model = "fake"
        self.protocol = LLMProtocol.CHAT_COMPLETIONS
        self.failures = failures
        self.text = text
        self.available = available
        self.calls = 0
        self.health_calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise LLMError(LLMErrorCategory.UNAVAILABLE, "unavailable")
        return LLMResponse(
            self.text,
            self.provider,
            self.model,
            self.protocol.value,
            LLMUsage(1, 1),
        )

    async def health(self) -> bool:
        self.health_calls += 1
        return self.available


async def test_service_retries_then_falls_back_and_health_is_ttl_cached() -> None:
    primary = _FakeTransport("primary", failures=2)
    fallback = _FakeTransport("fallback", text="fallback")
    service = LLMService(
        {"primary": primary, "fallback": fallback},
        routes={"default": {"provider": "primary"}},
        fallback_order=("primary", "fallback"),
        max_retries=1,
        availability_ttl_seconds=60,
    )
    try:
        response = await service.complete(LLMRequest(task="generic", prompt="hello"))
        assert await service.is_available() is True
        assert await service.is_available() is True
    finally:
        await service.close()

    assert response.text == "fallback"
    assert primary.calls == 2
    assert fallback.calls == 1
    assert primary.health_calls == 1


async def test_romanization_deduplicates_terms_into_one_request() -> None:
    transport = _FakeTransport(
        "local", text='[{"id": 0, "reading": "オープンエーアイ"}]'
    )
    service = LLMService(
        {"local": transport}, routes={"default": {"provider": "local"}}
    )
    try:
        converted = await service.romanize_many(
            ["OpenAIを使う。", "OpenAIと連携する。"]
        )
    finally:
        await service.close()

    assert converted == ["オープンエーアイを使う。", "オープンエーアイと連携する。"]
    assert transport.calls == 1


def test_token_budget_and_latest_catalog() -> None:
    budget = TokenBudget(context_tokens=1024, safety_ratio=0.8, max_items=2)
    batches = split_by_token_budget(["あ" * 10, "い" * 10, "う" * 10], budget)

    assert batches == [["あ" * 10, "い" * 10], ["う" * 10]]
    assert estimate_tokens("日本語ABC") > 0
    assert protocol_for_model("qwen3.8-max") == LLMProtocol.MESSAGES
    assert protocol_for_model("qwen3.8-flash") == LLMProtocol.MESSAGES


def test_legacy_ollama_config_is_migrated_without_secret_fields() -> None:
    with pytest.warns(DeprecationWarning):
        config = normalize_llm_config(
            {"ollama": {"url": "http://localhost:11434", "model": "legacy"}}
        )

    assert config["providers"]["local"] == {
        "type": "ollama_legacy",
        "base_url": "http://localhost:11434",
        "model": "legacy",
        "protocol": "ollama_chat",
    }


async def test_slack_rule_certain_case_uses_no_llm_request() -> None:
    transport = _FakeTransport("local", text="1.0")
    service = LLMService(
        {"local": transport}, routes={"default": {"provider": "local"}}
    )
    scorer = ImportanceScorer(
        service, mention_boost=0.6, threshold=0.6, owns_llm=True
    )
    message = SlackMessage("確認してください", "general", "user", "1", is_mention=True)

    try:
        score = await scorer.score(message)
    finally:
        await scorer.close()

    assert score == 0.6
    assert transport.calls == 0
