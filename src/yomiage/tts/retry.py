"""軽量非同期リトライ機構."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..api.exceptions import YomiageError

T = TypeVar("T")


@dataclass
class RetryConfig:
    """リトライ設定."""

    max_retries: int = 2
    backoff_factor: float = 0.5
    max_backoff: float = 30.0
    retryable_exceptions: tuple[type[Exception], ...] = field(
        default_factory=lambda: (Exception,)
    )


DEFAULT_RETRY_CONFIG = RetryConfig()


async def with_retry(
    coro_fn: Callable[[], Awaitable[T]],
    config: RetryConfig | None = None,
    operation_name: str = "operation",
) -> T:
    """コルーチンをリトライ付きで実行.

    Args:
        coro_fn: リトライ対象のコルーチンを返す callable
        config: リトライ設定
        operation_name: ログ用の操作名

    Returns:
        コルーチンの結果

    Raises:
        最後のリトライで発生した例外
    """
    cfg = config or DEFAULT_RETRY_CONFIG
    last_err: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return await coro_fn()
        except cfg.retryable_exceptions as e:
            last_err = e
            if attempt >= cfg.max_retries:
                break
            backoff = min(
                cfg.backoff_factor * (2 ** attempt),
                cfg.max_backoff,
            )
            logger.warning(
                f"{operation_name} failed (attempt {attempt + 1}/"
                f"{cfg.max_retries + 1}): {e}. Retrying in {backoff:.1f}s"
            )
            await asyncio.sleep(backoff)

    raise last_err or RuntimeError(f"{operation_name} failed")


def is_retryable(error: YomiageError) -> bool:
    """エラーがリトライ対象か判定."""
    from ..api.exceptions import (
        ProviderUnavailableError,
        RateLimitError,
        TimeoutError,
    )

    return isinstance(error, (ProviderUnavailableError, TimeoutError, RateLimitError))
