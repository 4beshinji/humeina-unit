"""設定からLLMServiceを構築するcomposition helper。"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

from loguru import logger

from .catalog import protocol_for_model
from .protocols import LLMProtocol, LLMTransport
from .service import LLMService
from .transports.chat_completions import ChatCompletionsTransport
from .transports.gemini import GeminiTransport
from .transports.messages import MessagesTransport
from .transports.ollama import OllamaTransport
from .transports.responses import ResponsesTransport

DEFAULT_LOCAL_MODEL = "Qwen3.8-27B"
_legacy_warning_emitted = False


def _secret(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if getter else value)


def normalize_llm_config(config: Mapping[str, Any] | Any) -> dict[str, Any]:
    """新形式を返し、旧ollama形式を互換Providerへ一度だけ変換する。"""

    if hasattr(config, "to_legacy_dict"):
        config = config.to_legacy_dict()
    if not isinstance(config, Mapping):
        raise ValueError("LLM configuration must be a mapping")
    llm = config.get("llm")
    if isinstance(llm, Mapping) and isinstance(llm.get("providers"), Mapping):
        return dict(llm)
    legacy = config.get("ollama")
    if not isinstance(legacy, Mapping) and isinstance(llm, Mapping):
        legacy = llm
    if isinstance(legacy, Mapping):
        global _legacy_warning_emitted
        if not _legacy_warning_emitted:
            logger.warning(
                "legacy 'ollama' LLM configuration is deprecated; use llm.providers"
            )
            _legacy_warning_emitted = True
        warnings.warn(
            "legacy 'ollama' LLM configuration is deprecated; use llm.providers",
            DeprecationWarning,
            stacklevel=3,
        )
        provider = {
            "type": "ollama_legacy",
            "base_url": legacy.get("url", "http://localhost:11434"),
            "model": legacy.get("model", "qwen3.5:9b"),
            "protocol": "ollama_chat",
        }
        return {
            "providers": {"local": provider},
            "routes": {"default": {"provider": "local"}},
            "fallback": {"order": ["local"], "on": ["unavailable", "timeout"]},
            "limits": {},
        }
    return {
        "providers": {
            "local": {
                "type": "llama_cpp",
                "base_url": "http://127.0.0.1:8080/v1",
                "model": DEFAULT_LOCAL_MODEL,
                "protocol": "chat_completions",
            }
        },
        "routes": {"default": {"provider": "local"}},
        "fallback": {"order": ["local"]},
        "limits": {},
    }


def _transport(name: str, data: Mapping[str, Any]) -> LLMTransport:
    provider_type = str(data.get("type", "llama_cpp"))
    model = str(data.get("model") or DEFAULT_LOCAL_MODEL)
    base_url = str(data.get("base_url") or data.get("url") or "").rstrip("/")
    api_key = _secret(data.get("api_key"))
    # 27B級の一部CPU offloadでは長い構造化出力が120秒を超える。cloudは短い
    # timeoutを維持し、標準local providerだけ十分な生成時間を確保する。
    default_timeout = 600.0 if provider_type == "llama_cpp" else 120.0
    timeout = float(data.get("timeout_seconds", default_timeout))
    common = {
        "provider": name,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "timeout_seconds": timeout,
    }
    if provider_type in {"ollama", "ollama_legacy"}:
        return OllamaTransport(**common)
    if provider_type == "gemini":
        return GeminiTransport(
            provider=name,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout,
        )
    protocol_value = str(data.get("protocol", "auto"))
    if protocol_value == "auto":
        if provider_type != "opencode_go":
            raise ValueError(f"provider '{name}' requires an explicit protocol")
        protocol = protocol_for_model(model)
    else:
        protocol = LLMProtocol(protocol_value)
    if protocol == LLMProtocol.CHAT_COMPLETIONS:
        return ChatCompletionsTransport(**common)
    if protocol == LLMProtocol.RESPONSES:
        return ResponsesTransport(**common)
    if protocol == LLMProtocol.MESSAGES:
        return MessagesTransport(**common)
    raise ValueError(f"unsupported protocol for provider '{name}': {protocol}")


def create_llm_service(
    config: Mapping[str, Any] | Any,
    *,
    metrics: Any | None = None,
) -> LLMService:
    """アプリケーション設定から共有LLMServiceを作成する。"""

    llm = normalize_llm_config(config)
    provider_data = llm.get("providers") or {}
    transports = {
        str(name): _transport(str(name), data)
        for name, data in provider_data.items()
        if isinstance(data, Mapping) and data.get("enabled", True)
    }
    fallback = llm.get("fallback") or {}
    limits = llm.get("limits") or {}
    return LLMService(
        transports,
        routes=dict(llm.get("routes") or {}),
        fallback_order=fallback.get("order") or tuple(transports),
        fallback_on=fallback.get("on")
        or fallback.get(True)
        or ("unavailable", "timeout", "rate_limit"),
        max_retries=int(limits.get("max_retries", 2)),
        availability_ttl_seconds=float(limits.get("availability_ttl_seconds", 60)),
        max_requests_per_job=int(limits.get("max_requests_per_job", 100)),
        max_input_tokens_per_job=int(limits.get("max_input_tokens_per_job", 500_000)),
        metrics=metrics,
    )


__all__ = ["DEFAULT_LOCAL_MODEL", "create_llm_service", "normalize_llm_config"]
