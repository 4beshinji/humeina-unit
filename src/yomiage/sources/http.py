"""URLポリシーを必ず通過する共通HTTP fetcher."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import re
import socket
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from ..ports.sources import (
    HTTPFetchError,
    HTTPResponse,
    HTTPStatusError,
    HTTPTransport,
    RedirectLimitError,
    RedirectTargetError,
    ResponseTooLargeError,
    URLPolicy,
    URLTarget,
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHARSET_RE = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"'\s]+)", re.IGNORECASE)


@dataclass(frozen=True)
class FetchedResponse:
    """redirectを終えた後の完全なHTTP応答."""

    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def content(self) -> bytes:
        """aiohttpの ``content`` に近い、本文bytesの短い別名."""

        return self.body

    def text(self, encoding: str | None = None) -> str:
        """Content-Type charsetを考慮して本文をdecodeする."""

        selected = encoding or _response_charset(self.headers) or "utf-8"
        try:
            return self.body.decode(selected)
        except (LookupError, UnicodeDecodeError):
            return self.body.decode("utf-8", errors="replace")


class _AiohttpResponse:
    """aiohttp responseをHTTPResponseポートへ適合させる."""

    def __init__(self, response: aiohttp.ClientResponse, session: aiohttp.ClientSession):
        self._response = response
        self._session = session
        self.status = response.status
        self.headers = response.headers

    async def iter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._response.content.read(chunk_size)
            if not chunk:
                break
            yield chunk

    async def close(self) -> None:
        self._response.release()
        try:
            await self._response.wait_for_close()
        finally:
            await self._session.close()


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """ポリシー検証済みIPだけをaiohttpへ渡すresolver.

    先に検証したhostnameをaiohttpが別のDNS問い合わせで再解決すると、DNS rebindingに
    よって検証と実接続の対象がずれる。requestごとにこのstatic resolverを作り、検証済み
    アドレスへ固定する。
    """

    def __init__(self, hostname: str, addresses: Sequence[ipaddress._BaseAddress]):
        self._hostname = hostname
        self._addresses = tuple(addresses)

    async def resolve(self, host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        if host.lower().rstrip(".") != self._hostname.lower().rstrip("."):
            return []
        results: list[dict[str, Any]] = []
        for address in self._addresses:
            if address.version == 4:
                if family == socket.AF_INET6:
                    continue
                sockaddr: tuple[Any, ...] = (str(address), port)
                address_family = socket.AF_INET
            else:
                if family == socket.AF_INET:
                    continue
                sockaddr = (str(address), port, 0, 0)
                address_family = socket.AF_INET6
            results.append(
                {
                    "hostname": self._hostname,
                    "host": str(address),
                    "port": port,
                    "family": address_family,
                    "proto": 0,
                    "flags": 0,
                    "sockaddr": sockaddr,
                }
            )
        return results

    async def close(self) -> None:
        return None


class AiohttpTransport:
    """検証済みIPへ接続する標準HTTP transport."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        addresses: Sequence[ipaddress._BaseAddress] = (),
    ) -> HTTPResponse:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        connector: aiohttp.TCPConnector | None = None
        if addresses:
            connector = aiohttp.TCPConnector(
                resolver=_PinnedResolver(hostname, addresses),
                use_dns_cache=False,
            )
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            connector=connector,
            connector_owner=True,
        )
        try:
            response = await session.request(
                method,
                url,
                headers=dict(headers or {}),
                allow_redirects=False,
            )
        except BaseException:
            await session.close()
            raise
        return _AiohttpResponse(response, session)


class HTTPFetcher:
    """共通のURL policy・redirect・size・timeoutを適用するfetcher."""

    def __init__(
        self,
        policy: URLPolicy | None = None,
        *,
        url_policy: URLPolicy | None = None,
        transport: HTTPTransport | Any | None = None,
        resolver: Any | None = None,
        chunk_size: int = 64 * 1024,
    ) -> None:
        if policy is not None and url_policy is not None:
            raise ValueError("pass either policy or url_policy, not both")
        policy = policy or url_policy
        if resolver is not None:
            if policy is None:
                policy = URLPolicy(resolver=resolver)
            elif policy.resolver is None:
                policy = URLPolicy(
                    allowed_schemes=policy.allowed_schemes,
                    allowed_ports=policy.allowed_ports,
                    max_response_bytes=policy.max_response_bytes,
                    max_redirects=policy.max_redirects,
                    timeout_seconds=policy.timeout_seconds,
                    allowlist=policy.allowlist,
                    allow_private_network=policy.allow_private_network,
                    resolver=resolver,
                )
        self.policy = policy or URLPolicy()
        self.transport: HTTPTransport | Any = transport or AiohttpTransport()
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        raise_for_status: bool = False,
    ) -> FetchedResponse:
        """URLを取得し、redirect先ごとにURL policyを再評価する."""

        current_url = url
        redirect_count = 0
        request_method = method.upper()

        while True:
            target = await self.policy.validate(current_url)
            response = await self._request(request_method, target, headers)
            try:
                if response.status in _REDIRECT_STATUSES:
                    if redirect_count >= self.policy.max_redirects:
                        raise RedirectLimitError(
                            f"Maximum redirects exceeded ({self.policy.max_redirects})"
                        )
                    location = _header(response.headers, "location")
                    if not location:
                        raise RedirectTargetError(
                            f"Redirect response has no Location header: {target.url}"
                        )
                    try:
                        next_url = urljoin(target.url, location)
                    except ValueError as exc:
                        raise RedirectTargetError(f"Invalid redirect target: {location}") from exc
                    # Validate immediately before requesting the next hop. The loop resolves DNS
                    # again, detecting a resolver result that changed between hops.
                    current_url = next_url
                    redirect_count += 1
                    continue

                response_status = response.status
                response_headers = dict(response.headers)
                body = await self._read_body(response, target.url, request_method)
            finally:
                await _close_response(response, timeout=self.policy.timeout_seconds)

            result = FetchedResponse(target.url, response_status, response_headers, body)
            if raise_for_status and not 200 <= result.status < 300:
                raise HTTPStatusError(result.status, result.url)
            return result

    async def fetch_bytes(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        raise_for_status: bool = False,
    ) -> bytes:
        """本文をbytesとして取得する."""

        response = await self.fetch(url, headers=headers, raise_for_status=raise_for_status)
        return response.body

    async def fetch_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        encoding: str | None = None,
        raise_for_status: bool = False,
    ) -> str:
        """本文をテキストとして取得する."""

        response = await self.fetch(url, headers=headers, raise_for_status=raise_for_status)
        return response.text(encoding)

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        raise_for_status: bool = False,
    ) -> FetchedResponse:
        """GETの短い別名."""

        return await self.fetch(url, headers=headers, raise_for_status=raise_for_status)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        raise_for_status: bool = False,
    ) -> FetchedResponse:
        """任意HTTP methodの取得."""

        return await self.fetch(
            url,
            method=method,
            headers=headers,
            raise_for_status=raise_for_status,
        )

    async def _request(
        self,
        method: str,
        target: URLTarget,
        headers: Mapping[str, str] | None,
    ) -> HTTPResponse:
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self.policy.timeout_seconds,
        }
        request_fn = getattr(self.transport, "request", None)
        if request_fn is None:
            if not callable(self.transport):
                raise HTTPFetchError("HTTP transport must expose request() or be callable")
            request_fn = self.transport
        if _accepts_keyword(request_fn, "addresses"):
            kwargs["addresses"] = target.addresses

        try:
            async with asyncio.timeout(self.policy.timeout_seconds):
                result = request_fn(method, target.url, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
        except TimeoutError as exc:
            raise HTTPFetchError(f"HTTP request timed out: {target.url}") from exc
        except HTTPFetchError:
            raise
        except Exception as exc:
            raise HTTPFetchError(f"HTTP request failed for {target.url}: {exc}") from exc

    async def _read_body(
        self,
        response: HTTPResponse,
        url: str,
        method: str,
    ) -> bytes:
        if method == "HEAD":
            return b""
        content_length = _content_length(response.headers)
        if content_length is not None and content_length > self.policy.max_response_bytes:
            raise ResponseTooLargeError(
                f"Response exceeds {self.policy.max_response_bytes} bytes: {url}"
            )

        collected = bytearray()
        try:
            iterator = _iter_response_bytes(response, self.chunk_size)
            async with asyncio.timeout(self.policy.timeout_seconds):
                async for chunk in iterator:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise HTTPFetchError("HTTP transport returned a non-bytes chunk")
                    collected.extend(chunk)
                    if len(collected) > self.policy.max_response_bytes:
                        raise ResponseTooLargeError(
                            f"Response exceeds {self.policy.max_response_bytes} bytes: {url}"
                        )
        except TimeoutError as exc:
            raise HTTPFetchError(f"HTTP response timed out: {url}") from exc
        return bytes(collected)


async def _iter_response_bytes(response: HTTPResponse, chunk_size: int) -> AsyncIterator[bytes]:
    iterator = getattr(response, "iter_bytes", None)
    if iterator is not None:
        result = iterator(chunk_size)
        if hasattr(result, "__aiter__"):
            async for chunk in result:
                yield bytes(chunk)
            return
        if inspect.isawaitable(result):
            result = await result
        for chunk in result:
            yield bytes(chunk)
        return

    content = getattr(response, "content", None)
    if content is not None and hasattr(content, "iter_chunked"):
        async for chunk in content.iter_chunked(chunk_size):
            yield bytes(chunk)
        return

    read = getattr(response, "read", None)
    if read is not None:
        result = read()
        if inspect.isawaitable(result):
            result = await result
        yield bytes(result)
        return

    body = getattr(response, "body", None)
    if body is not None:
        yield bytes(body)
        return
    raise HTTPFetchError("HTTP transport response has no readable body")


async def _close_response(response: Any, *, timeout: float | None = None) -> None:
    close = getattr(response, "close", None)
    if close is None:
        return
    try:
        if timeout is None:
            result = close()
            if inspect.isawaitable(result):
                await result
        else:
            async with asyncio.timeout(timeout):
                result = close()
                if inspect.isawaitable(result):
                    await result
    except TimeoutError:
        return
    except Exception:
        # 本文取得結果を優先し、close失敗で成功応答を隠さない。
        return


def _accepts_keyword(callable_obj: Any, keyword: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    parameters = signature.parameters.values()
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return str(value)
        value = headers.get(name.title())
        if value is not None:
            return str(value)
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return None


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = _header(headers, "content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _response_charset(headers: Mapping[str, str]) -> str | None:
    value = _header(headers, "content-type")
    if not value:
        return None
    match = _CHARSET_RE.search(value)
    return match.group(1) if match else None


# Common names used by call sites and external integrations.
SecureHTTPFetcher = HTTPFetcher
CommonHTTPFetcher = HTTPFetcher
URLFetcher = HTTPFetcher


__all__ = [
    "AiohttpTransport",
    "CommonHTTPFetcher",
    "FetchedResponse",
    "HTTPFetcher",
    "SecureHTTPFetcher",
    "URLFetcher",
]
