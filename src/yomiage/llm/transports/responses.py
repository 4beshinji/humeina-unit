"""OpenAI Responses API Transport。"""

from __future__ import annotations

from typing import Any

from ..models import LLMError, LLMErrorCategory, LLMRequest, LLMResponse, LLMUsage
from ..protocols import LLMProtocol
from .http import HTTPTransport


class ResponsesTransport(HTTPTransport):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(protocol=LLMProtocol.RESPONSES, **kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "input": request.prompt,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.system:
            body["instructions"] = request.system
        if request.response_mode == "json" and request.json_schema:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": request.json_schema,
                    "strict": True,
                }
            }
        payload, latency = await self._post("/responses", body)
        text = payload.get("output_text")
        if not isinstance(text, str):
            parts: list[str] = []
            for output in payload.get("output") or []:
                if not isinstance(output, dict):
                    continue
                for content in output.get("content") or []:
                    if isinstance(content, dict) and content.get("type") in {
                        "output_text",
                        "text",
                    }:
                        parts.append(str(content.get("text", "")))
            text = "".join(parts)
        if not text:
            raise LLMError(
                LLMErrorCategory.INVALID_RESPONSE,
                f"{self.provider} response has no output text",
            )
        raw_usage = payload.get("usage") or {}
        details = raw_usage.get("input_tokens_details") or {}
        usage = LLMUsage(
            input_tokens=int(raw_usage.get("input_tokens", 0) or 0),
            output_tokens=int(raw_usage.get("output_tokens", 0) or 0),
            cached_input_tokens=int(details.get("cached_tokens", 0) or 0),
        )
        return self._response(text, usage, latency)

    async def count_tokens(self, text: str) -> int | None:
        try:
            payload, _ = await self._post(
                "/responses/input_tokens", {"model": self.model, "input": text}
            )
        except LLMError:
            return None
        value = payload.get("input_tokens") or payload.get("tokens")
        return int(value) if value is not None else None
