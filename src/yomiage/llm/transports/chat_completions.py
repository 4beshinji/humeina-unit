"""OpenAI Chat Completions互換Transport。"""

from __future__ import annotations

from typing import Any

from ..models import LLMError, LLMErrorCategory, LLMRequest, LLMResponse, LLMUsage
from ..protocols import LLMProtocol
from .http import HTTPTransport


class ChatCompletionsTransport(HTTPTransport):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(protocol=LLMProtocol.CHAT_COMPLETIONS, **kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.response_mode == "json":
            if request.json_schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response",
                        "schema": request.json_schema,
                        "strict": True,
                    },
                }
        payload, latency = await self._post("/chat/completions", body)
        try:
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", "")) for item in content if isinstance(item, dict)
                )
            text = str(content)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                LLMErrorCategory.INVALID_RESPONSE,
                f"{self.provider} response has no message content",
            ) from exc
        raw_usage = payload.get("usage") or {}
        details = raw_usage.get("prompt_tokens_details") or {}
        usage = LLMUsage(
            input_tokens=int(raw_usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(raw_usage.get("completion_tokens", 0) or 0),
            cached_input_tokens=int(details.get("cached_tokens", 0) or 0),
        )
        return self._response(text, usage, latency)

    async def count_tokens(self, text: str) -> int | None:
        try:
            payload, _ = await self._post(
                "/chat/completions/input_tokens",
                {"model": self.model, "messages": [{"role": "user", "content": text}]},
            )
        except LLMError:
            return None
        value = payload.get("tokens") or payload.get("input_tokens")
        return int(value) if value is not None else None

    async def health(self) -> bool:
        # llama-serverは/v1を除いた/healthを公開する。OpenAI互換Providerでは
        # /modelsへfallbackし、catalog内に設定modelがあることも確認する。
        if self.base_url.endswith("/v1"):
            health = await self._get_url(f"{self.base_url[:-3]}/health")
            if health is not None:
                return True
        return await super().health()
