"""LLM HTTP Transport共通処理。"""

from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any

import aiohttp

from ..models import LLMError, LLMErrorCategory, LLMResponse, LLMUsage
from ..protocols import LLMProtocol, LLMTransport


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - __import__("time").time())
        except (TypeError, ValueError):
            return None


def _error_category(status: int, body: str) -> LLMErrorCategory:
    lowered = body.lower()
    if status in {401, 403}:
        return LLMErrorCategory.AUTHENTICATION
    if status == 429:
        return LLMErrorCategory.RATE_LIMIT
    if status in {500, 502, 503, 504}:
        return LLMErrorCategory.SERVER
    if status in {408}:
        return LLMErrorCategory.TIMEOUT
    if status in {400, 413, 422} and any(
        marker in lowered for marker in ("context", "token limit", "too long", "maximum")
    ):
        return LLMErrorCategory.CONTEXT_LENGTH
    if 400 <= status < 500:
        return LLMErrorCategory.INVALID_REQUEST
    return LLMErrorCategory.UNKNOWN


class HTTPTransport(LLMTransport):
    """認証、timeout、status分類、session lifecycleを共有する基底。"""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        protocol: LLMProtocol,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 120.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.protocol = protocol
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds)
            )
            self._owns_session = True
        return self._session

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _post(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], float]:
        started = perf_counter()
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}{path}", json=body, headers=self._headers()
            ) as response:
                if response.status < 200 or response.status >= 300:
                    detail = (await response.text())[:1000]
                    category = _error_category(response.status, detail)
                    raise LLMError(
                        category,
                        f"{self.provider} request failed with HTTP {response.status}",
                        status=response.status,
                        retry_after=_retry_after(response.headers.get("Retry-After")),
                    )
                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise LLMError(
                        LLMErrorCategory.INVALID_RESPONSE,
                        f"{self.provider} returned non-JSON data",
                    ) from exc
        except LLMError:
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise LLMError(LLMErrorCategory.TIMEOUT, f"{self.provider} timed out") from exc
        except aiohttp.ClientError as exc:
            raise LLMError(
                LLMErrorCategory.UNAVAILABLE, f"{self.provider} is unavailable"
            ) from exc
        if not isinstance(payload, dict):
            raise LLMError(
                LLMErrorCategory.INVALID_RESPONSE,
                f"{self.provider} response must be an object",
            )
        return payload, (perf_counter() - started) * 1000

    async def _get_url(self, url: str) -> dict[str, Any] | None:
        try:
            session = await self._get_session()
            async with session.get(url, headers=self._headers()) as response:
                if response.status < 200 or response.status >= 300:
                    return None
                if response.content_type == "application/json":
                    value = await response.json()
                    return value if isinstance(value, dict) else {}
                return {}
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError):
            return None

    async def _get(self, path: str) -> dict[str, Any] | None:
        return await self._get_url(f"{self.base_url}{path}")

    def _response(
        self, text: str, usage: LLMUsage, latency_ms: float
    ) -> LLMResponse:
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            protocol=self.protocol.value,
            usage=usage,
            latency_ms=latency_ms,
        )

    async def health(self) -> bool:
        payload = await self._get("/models")
        if payload is None:
            return False
        models = payload.get("data")
        if isinstance(models, list) and models:
            ids = {
                str(item.get("id"))
                for item in models
                if isinstance(item, dict) and item.get("id")
            }
            return not ids or self.model in ids or self.model.removeprefix("opencode-go/") in ids
        return True

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
