"""標準Providerごとのtask出力予算。"""

from __future__ import annotations

CLOUD_ROUTE_OUTPUT_TOKENS: dict[str, int] = {
    "default": 16384,
    "scene_analysis": 16384,
    "character_analysis": 16384,
    "romanization": 8192,
    "news_summary": 16384,
    "urgency_scoring": 4096,
    "slack_scoring": 4096,
    "ex_voice": 8192,
}

LOCAL_ROUTE_OUTPUT_TOKENS: dict[str, int] = {
    "default": 4096,
    "scene_analysis": 8192,
    "character_analysis": 8192,
    "romanization": 2048,
    "news_summary": 4096,
    "urgency_scoring": 1024,
    "slack_scoring": 1024,
    "ex_voice": 2048,
}

ROUTE_OUTPUT_TOKENS_BY_PROVIDER = {
    "opencode_go": CLOUD_ROUTE_OUTPUT_TOKENS,
    "local": LOCAL_ROUTE_OUTPUT_TOKENS,
}

__all__ = [
    "CLOUD_ROUTE_OUTPUT_TOKENS",
    "LOCAL_ROUTE_OUTPUT_TOKENS",
    "ROUTE_OUTPUT_TOKENS_BY_PROVIDER",
]
