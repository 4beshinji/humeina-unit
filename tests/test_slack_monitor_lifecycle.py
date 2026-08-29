"""E04 の外部検証前に固定できる Slack Socket Mode cleanup 契約。"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest

from yomiage.slack.monitor import SlackMonitor


def _package(name: str) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = []  # type: ignore[attr-defined]
    return module


@pytest.mark.asyncio
async def test_e04_monitor_cancel_closes_owned_socket_client_once(monkeypatch) -> None:
    connected = asyncio.Event()
    clients = []

    class FakeAsyncWebClient:
        def __init__(self, *, token: str) -> None:
            self.token = token

    class FakeSocketModeClient:
        def __init__(self, *, app_token: str, web_client: object) -> None:
            self.app_token = app_token
            self.web_client = web_client
            self.socket_mode_request_listeners = []
            self.close_calls = 0
            clients.append(self)

        async def connect(self) -> None:
            connected.set()

        async def close(self) -> None:
            self.close_calls += 1

        async def send_socket_mode_response(self, _response: object) -> None:
            return None

    class FakeSocketModeRequest:
        pass

    class FakeSocketModeResponse:
        def __init__(self, *, envelope_id: str) -> None:
            self.envelope_id = envelope_id

    modules = {
        "slack_sdk": _package("slack_sdk"),
        "slack_sdk.socket_mode": _package("slack_sdk.socket_mode"),
        "slack_sdk.web": _package("slack_sdk.web"),
        "slack_sdk.socket_mode.aiohttp": ModuleType("slack_sdk.socket_mode.aiohttp"),
        "slack_sdk.socket_mode.request": ModuleType("slack_sdk.socket_mode.request"),
        "slack_sdk.socket_mode.response": ModuleType("slack_sdk.socket_mode.response"),
        "slack_sdk.web.async_client": ModuleType("slack_sdk.web.async_client"),
    }
    modules["slack_sdk.socket_mode.aiohttp"].SocketModeClient = FakeSocketModeClient
    modules["slack_sdk.socket_mode.request"].SocketModeRequest = FakeSocketModeRequest
    modules["slack_sdk.socket_mode.response"].SocketModeResponse = FakeSocketModeResponse
    modules["slack_sdk.web.async_client"].AsyncWebClient = FakeAsyncWebClient
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    monitor = SlackMonitor("bot-token", "app-token")
    task = asyncio.create_task(monitor.start())
    await asyncio.wait_for(connected.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(clients) == 1
    assert clients[0].close_calls == 1
