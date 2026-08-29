"""Google Gemini generateContent Adapter。"""

from __future__ import annotations

from typing import Any

from ..models import LLMError, LLMErrorCategory, LLMRequest, LLMResponse, LLMUsage
from ..protocols import LLMProtocol
from .http import HTTPTransport


class GeminiTransport(HTTPTransport):
    def __init__(self, *, api_key: str, model: str, **kwargs: Any) -> None:
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        super().__init__(
            protocol=LLMProtocol.GEMINI,
            api_key="",
            model=model,
            base_url=endpoint,
            **kwargs,
        )
        self._configured = bool(api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": request.prompt}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if request.system:
            body["system_instruction"] = {"parts": [{"text": request.system}]}
        if request.response_mode == "json":
            body["generationConfig"]["responseMimeType"] = "application/json"
            if request.json_schema:
                body["generationConfig"]["responseSchema"] = request.json_schema
        payload, latency = await self._post("", body)
        try:
            text = "".join(
                str(part.get("text", ""))
                for part in payload["candidates"][0]["content"]["parts"]
                if isinstance(part, dict)
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                LLMErrorCategory.INVALID_RESPONSE,
                "gemini response has no text content",
            ) from exc
        usage_meta = payload.get("usageMetadata") or {}
        usage = LLMUsage(
            input_tokens=int(usage_meta.get("promptTokenCount", 0) or 0),
            output_tokens=int(usage_meta.get("candidatesTokenCount", 0) or 0),
            cached_input_tokens=int(usage_meta.get("cachedContentTokenCount", 0) or 0),
        )
        return self._response(text, usage, latency)

    async def health(self) -> bool:
        return self._configured
