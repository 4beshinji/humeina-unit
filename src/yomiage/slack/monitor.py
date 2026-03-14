"""Slack WebSocket/API monitor."""

import asyncio
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class SlackMessage:
    """Slackメッセージ."""

    text: str
    channel: str
    user: str
    timestamp: str
    is_mention: bool = False
    reaction_count: int = 0
    metadata: dict = field(default_factory=dict)


class SlackMonitor:
    """Slack Socket Mode で指定チャンネルを監視."""

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        channels: list[str] | None = None,
    ):
        self.bot_token = bot_token
        self.app_token = app_token
        self.channels = channels or []
        self._running = False
        self._callback = None

    def on_message(self, callback) -> None:
        """メッセージ受信コールバック設定."""
        self._callback = callback

    async def start(self) -> None:
        """監視開始."""
        if not self.bot_token or not self.app_token:
            logger.error("Slack tokens not configured")
            return

        self._running = True
        logger.info(f"Slack monitor starting (channels={self.channels})")

        try:
            from slack_sdk.socket_mode.aiohttp import SocketModeClient
            from slack_sdk.web.async_client import AsyncWebClient

            web_client = AsyncWebClient(token=self.bot_token)
            socket_client = SocketModeClient(
                app_token=self.app_token, web_client=web_client
            )

            from slack_sdk.socket_mode.request import SocketModeRequest
            from slack_sdk.socket_mode.response import SocketModeResponse

            async def handler(client: SocketModeClient, req: SocketModeRequest):
                if req.type == "events_api":
                    event = req.payload.get("event", {})
                    if event.get("type") == "message" and "subtype" not in event:
                        channel = event.get("channel", "")
                        if not self.channels or channel in self.channels:
                            msg = SlackMessage(
                                text=event.get("text", ""),
                                channel=channel,
                                user=event.get("user", ""),
                                timestamp=event.get("ts", ""),
                                is_mention="<@" in event.get("text", ""),
                            )
                            if self._callback:
                                await self._callback(msg)
                    await client.send_socket_mode_response(
                        SocketModeResponse(envelope_id=req.envelope_id)
                    )

            socket_client.socket_mode_request_listeners.append(handler)
            await socket_client.connect()

            while self._running:
                await asyncio.sleep(1)

        except ImportError:
            logger.error("slack-sdk not installed. Run: pip install 'voisona-yomiage[slack]'")
        except Exception as e:
            logger.error(f"Slack monitor error: {e}")

    def stop(self) -> None:
        self._running = False
        logger.info("Slack monitor stopped")
