"""OpenCode GoのモデルIDとAPI形式のcatalog。"""

from __future__ import annotations

from .protocols import LLMProtocol

# 公式 https://opencode.ai/docs/go/ の2026-08-29一覧。
# 未知モデルは /models のmetadataまたは明示protocolで解決する。
OPENCODE_GO_PROTOCOLS: dict[str, LLMProtocol] = {
    "grok-4.6": LLMProtocol.RESPONSES,
    "gpt-5.6-luna": LLMProtocol.RESPONSES,
    "muse-spark-1.2-contributor": LLMProtocol.RESPONSES,
    "glm-5.3-flash": LLMProtocol.CHAT_COMPLETIONS,
    "glm-5.3": LLMProtocol.CHAT_COMPLETIONS,
    "glm-5.2": LLMProtocol.CHAT_COMPLETIONS,
    "glm-5.1": LLMProtocol.CHAT_COMPLETIONS,
    "kimi-k3": LLMProtocol.CHAT_COMPLETIONS,
    "kimi-k2.7-code": LLMProtocol.CHAT_COMPLETIONS,
    "kimi-k2.6": LLMProtocol.CHAT_COMPLETIONS,
    "longcat-2.0": LLMProtocol.CHAT_COMPLETIONS,
    "deepseek-v4-pro": LLMProtocol.CHAT_COMPLETIONS,
    "deepseek-v4-flash": LLMProtocol.CHAT_COMPLETIONS,
    "deepseek-v4-flash-vision-exp": LLMProtocol.CHAT_COMPLETIONS,
    "mimo-v2.5": LLMProtocol.CHAT_COMPLETIONS,
    "mimo-v2.5-pro": LLMProtocol.CHAT_COMPLETIONS,
    "hy4-preview": LLMProtocol.CHAT_COMPLETIONS,
    "hy3": LLMProtocol.CHAT_COMPLETIONS,
    "minimax-m3": LLMProtocol.MESSAGES,
    "minimax-m2.7": LLMProtocol.MESSAGES,
    "minimax-m2.5": LLMProtocol.MESSAGES,
    "qwen3.8-max": LLMProtocol.MESSAGES,
    "qwen3.8-flash": LLMProtocol.MESSAGES,
    "qwen3.7-max": LLMProtocol.MESSAGES,
    "qwen3.7-plus": LLMProtocol.MESSAGES,
    "qwen3.6-plus": LLMProtocol.MESSAGES,
}


def protocol_for_model(model: str, override: str = "auto") -> LLMProtocol:
    """明示設定を優先し、autoでは既知catalogを参照する。"""

    if override != "auto":
        return LLMProtocol(override)
    model_id = model.removeprefix("opencode-go/")
    try:
        return OPENCODE_GO_PROTOCOLS[model_id]
    except KeyError as exc:
        raise ValueError(
            f"OpenCode Go model '{model_id}' is not in the protocol catalog; "
            "set protocol explicitly"
        ) from exc


__all__ = ["OPENCODE_GO_PROTOCOLS", "protocol_for_model"]
