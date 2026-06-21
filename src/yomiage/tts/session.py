"""aiohttp ClientSession の共有管理."""

from __future__ import annotations

import asyncio

import aiohttp
from loguru import logger


class SharedSessionManager:
    """複数 TTSProvider 間で aiohttp.ClientSession を共有する.

    通常は 1 プロセスあたり 1 インスタンスを使う。
    コンテキストマネージャーとして利用し、終了時にセッションをクローズする。
    テスト等でイベントループが切り替わった場合はセッションを再作成する。
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._ref_count: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        if (
            self._session is None
            or self._session.closed
            or self._loop is not loop
        ):
            old_session = self._session
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
            )
            self._loop = loop
            logger.debug("Created shared aiohttp ClientSession")
            if old_session is not None and not old_session.closed:
                try:
                    loop.create_task(old_session.close())
                except Exception:
                    old_session.close()
        return self._session

    def acquire(self) -> aiohttp.ClientSession:
        self._ref_count += 1
        return self.session

    async def release(self) -> None:
        self._ref_count = max(0, self._ref_count - 1)
        if self._ref_count == 0 and self._session and not self._session.closed:
            logger.debug("Closing shared aiohttp ClientSession")
            await self._session.close()
            self._session = None
            self._loop = None

    async def __aenter__(self) -> aiohttp.ClientSession:
        return self.acquire()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release()


# プロセス単位のデフォルト共有セッションマネージャー
_default_session_manager: SharedSessionManager | None = None


def get_default_session_manager() -> SharedSessionManager:
    global _default_session_manager
    if _default_session_manager is None:
        _default_session_manager = SharedSessionManager()
    return _default_session_manager
