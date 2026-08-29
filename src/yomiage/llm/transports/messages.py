"""Anthropic Messages API互換Transport。"""

from __future__ import annotations

from typing import Any

from ..models import LLMError, LLMErrorCategory, LLMRequest, LLMResponse, LLMUsage
from ..protocols import LLMProtocol
from .http import HTTPTransport


class MessagesTransport(HTTPTransport):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(protocol=LLMProtocol.MESSAGES, **kwargs)

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        headers["anthropic-version"] = "2023-06-01"
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.system:
            body["system"] = request.system
        if request.response_mode == "json":
            schema_note = "JSONのみを返してください。"
            if request.json_schema:
                import json

                schema_note += " schema: " + json.dumps(
                    request.json_schema, ensure_ascii=False, separators=(",", ":")
                )
            body["messages"][0]["content"] = f"{request.prompt}\n\n{schema_note}"
        payload, latency = await self._post("/messages", body)
        parts = [
            str(item.get("text", ""))
            for item in payload.get("content") or []
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "".join(parts)
        if not text:
            raise LLMError(
                LLMErrorCategory.INVALID_RESPONSE,
                f"{self.provider} response has no text content",
            )
        raw_usage = payload.get("usage") or {}
        usage = LLMUsage(
            input_tokens=int(raw_usage.get("input_tokens", 0) or 0),
            output_tokens=int(raw_usage.get("output_tokens", 0) or 0),
            cached_input_tokens=int(raw_usage.get("cache_read_input_tokens", 0) or 0),
        )
        return self._response(text, usage, latency)

    async def count_tokens(self, text: str) -> int | None:
        try:
            payload, _ = await self._post(
                "/messages/count_tokens",
                {"model": self.model, "messages": [{"role": "user", "content": text}]},
            )
        except LLMError:
            return None
        value = payload.get("input_tokens")
        return int(value) if value is not None else None
