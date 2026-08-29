"""移行期間用Ollama /api/chat Transport。"""

from __future__ import annotations

from typing import Any

from ..models import LLMError, LLMErrorCategory, LLMRequest, LLMResponse, LLMUsage
from ..protocols import LLMProtocol
from .http import HTTPTransport


class OllamaTransport(HTTPTransport):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(protocol=LLMProtocol.OLLAMA_CHAT, **kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
            },
        }
        if request.response_mode == "json":
            body["format"] = request.json_schema or "json"
        payload, latency = await self._post("/api/chat", body)
        try:
            text = str(payload["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError(
                LLMErrorCategory.INVALID_RESPONSE,
                "ollama response has no message content",
            ) from exc
        usage = LLMUsage(
            input_tokens=int(payload.get("prompt_eval_count", 0) or 0),
            output_tokens=int(payload.get("eval_count", 0) or 0),
        )
        return self._response(text, usage, latency)

    async def health(self) -> bool:
        return await self._get("/api/tags") is not None
