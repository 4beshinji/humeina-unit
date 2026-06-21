"""Event hooks for SDK users to observe TTS and batch pipeline lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from loguru import logger


class EventType(str, Enum):
    """SDK イベント種別."""

    SYNTHESIS_START = "synthesis:start"
    SYNTHESIS_END = "synthesis:end"
    SYNTHESIS_ERROR = "synthesis:error"
    SYNTHESIS_CACHE_HIT = "synthesis:cache_hit"
    BATCH_JOB_STATUS = "batch:job_status"


@dataclass
class Event:
    """SDK イベント."""

    type: EventType
    payload: dict[str, Any]


EventHandler = Callable[[Event], Any]


class EventHooks:
    """合成・バッチパイプラインのイベントフック登録・発火."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = {
            event_type: [] for event_type in EventType
        }

    def on(
        self,
        event_type: EventType,
        handler: EventHandler | None = None,
    ) -> EventHandler | Callable[[EventHandler], EventHandler]:
        """イベントハンドラーを登録する.

        Returns:
            登録されたハンドラー（デコレータとしても使える）.
        """

        def _register(h: EventHandler) -> EventHandler:
            self._handlers[event_type].append(h)
            return h

        if handler is None:
            return _register
        return _register(handler)

    def off(self, event_type: EventType, handler: EventHandler) -> None:
        """イベントハンドラーを解除する."""
        self._handlers[event_type].remove(handler)

    def emit(self, event: Event) -> None:
        """イベントを発火する."""
        for handler in self._handlers.get(event.type, []):
            try:
                handler(event)
            except Exception as exc:  # pragma: no cover - ハンドラー失敗を許容
                logger.warning(f"Event handler for {event.type} failed: {exc}")

    def emit_synthesis_start(
        self, text: str, engine: str, params: dict[str, Any]
    ) -> None:
        self.emit(
            Event(
                EventType.SYNTHESIS_START,
                {"text": text, "engine": engine, "params": params},
            )
        )

    def emit_synthesis_end(
        self,
        text: str,
        engine: str,
        params: dict[str, Any],
        duration_ms: float,
        cache_hit: bool,
    ) -> None:
        self.emit(
            Event(
                EventType.SYNTHESIS_END,
                {
                    "text": text,
                    "engine": engine,
                    "params": params,
                    "duration_ms": duration_ms,
                    "cache_hit": cache_hit,
                },
            )
        )

    def emit_synthesis_error(
        self, text: str, engine: str, params: dict[str, Any], error: str
    ) -> None:
        self.emit(
            Event(
                EventType.SYNTHESIS_ERROR,
                {"text": text, "engine": engine, "params": params, "error": error},
            )
        )

    def emit_cache_hit(self, text: str, engine: str, params: dict[str, Any]) -> None:
        self.emit(
            Event(
                EventType.SYNTHESIS_CACHE_HIT,
                {"text": text, "engine": engine, "params": params},
            )
        )

    def emit_batch_job_status(
        self,
        job_id: str,
        status: str,
        percent: float,
        message: str,
        output_path: str | None = None,
        error: str | None = None,
    ) -> None:
        self.emit(
            Event(
                EventType.BATCH_JOB_STATUS,
                {
                    "job_id": job_id,
                    "status": status,
                    "percent": percent,
                    "message": message,
                    "output_path": output_path,
                    "error": error,
                },
            )
        )
