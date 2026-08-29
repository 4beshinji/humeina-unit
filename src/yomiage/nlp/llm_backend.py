"""旧import path向けLLM互換re-export。

新規コードは :mod:`yomiage.llm` を参照する。ここには既存SDKのclass名と
factoryだけを残し、実際のHTTP処理は共通Transportへ委譲する。
"""

from __future__ import annotations

from ..llm import LLMBackend, LLMService
from ..llm.factory import create_llm_service
from ..llm.transports import (
    ChatCompletionsTransport,
    MessagesTransport,
    OllamaTransport,
)
from .ollama_client import OllamaClient


class OllamaBackend(LLMService):
    """移行期間用Ollama互換Backend。"""

    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "qwen3.5:9b",
    ) -> None:
        self._client = OllamaClient(url=url, model=model)
        transport = OllamaTransport(
            provider="ollama_legacy", model=model, base_url=url
        )
        super().__init__(
            {"ollama_legacy": transport},
            routes={"default": {"provider": "ollama_legacy"}},
            fallback_order=("ollama_legacy",),
        )

    @property
    def client(self) -> OllamaClient:
        """旧OllamaClientを参照する既存integrator向け。"""

        return self._client


class OpenAIBackend(LLMService):
    """OpenAI Chat Completions互換Backend。"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        transport = ChatCompletionsTransport(
            provider="openai", model=model, base_url=base_url, api_key=api_key
        )
        super().__init__(
            {"openai": transport},
            routes={"default": {"provider": "openai"}},
            fallback_order=("openai",),
        )


class AnthropicBackend(LLMService):
    """Anthropic Messages互換Backend。"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        base_url: str = "https://api.anthropic.com/v1",
    ) -> None:
        transport = MessagesTransport(
            provider="anthropic", model=model, base_url=base_url, api_key=api_key
        )
        super().__init__(
            {"anthropic": transport},
            routes={"default": {"provider": "anthropic"}},
            fallback_order=("anthropic",),
        )


def create_llm_backend(
    backend: str = "llama_cpp",
    *,
    url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    protocol: str | None = None,
    **kwargs: object,
) -> LLMBackend:
    """旧factory APIから共通LLMServiceを構築する。"""

    if backend == "ollama":
        return OllamaBackend(
            url=url or "http://localhost:11434",
            model=model or "qwen3.5:9b",
        )
    if backend == "openai":
        if not api_key:
            raise ValueError("OpenAI backend requires api_key")
        return OpenAIBackend(
            api_key=api_key,
            model=model or "gpt-4o-mini",
            base_url=url or "https://api.openai.com/v1",
        )
    if backend == "anthropic":
        if not api_key:
            raise ValueError("Anthropic backend requires api_key")
        return AnthropicBackend(
            api_key=api_key,
            model=model or "claude-sonnet-4-20250514",
            base_url=url or "https://api.anthropic.com/v1",
        )
    if backend in {"llama_cpp", "opencode_go"}:
        provider = {
            "type": backend,
            "base_url": url
            or (
                "https://opencode.ai/zen/go/v1"
                if backend == "opencode_go"
                else "http://127.0.0.1:8080/v1"
            ),
            "api_key": api_key or "",
            "model": model
            or ("qwen3.8-flash" if backend == "opencode_go" else "Qwen3.8-27B"),
            "protocol": protocol
            or ("auto" if backend == "opencode_go" else "chat_completions"),
        }
        return create_llm_service(
            {
                "llm": {
                    "providers": {"default": provider},
                    "routes": {"default": {"provider": "default"}},
                    "fallback": {"order": ["default"]},
                }
            },
            metrics=kwargs.get("metrics"),
        )
    raise ValueError(f"Unknown LLM backend: {backend}")


__all__ = [
    "AnthropicBackend",
    "LLMBackend",
    "OllamaBackend",
    "OpenAIBackend",
    "create_llm_backend",
]
