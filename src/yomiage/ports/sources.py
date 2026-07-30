"""コンテンツ取得に関する安全境界とポート.

このモジュールは、ContentSource が外部ネットワークへアクセスする際に共有する契約を
定義する。特定サイトの実装が ``aiohttp`` を直接利用すると、URL検証・redirect検証・
取得上限を迂回できるため、HTTPの実装詳細は ``yomiage.sources.http`` に隔離する。
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence
from urllib.parse import SplitResult, urldefrag, urlsplit


class URLPolicyError(ValueError):
    """URLまたは解決先が取得ポリシーに違反した場合のエラー."""


class DNSResolutionError(URLPolicyError):
    """ホスト名を安全に解決できなかった場合のエラー."""


class HTTPFetchError(RuntimeError):
    """HTTP transportまたは応答処理のエラー."""


class ResponseTooLargeError(HTTPFetchError):
    """応答本文が設定された最大サイズを超えた場合のエラー."""


class RedirectLimitError(HTTPFetchError):
    """redirect回数が上限を超えた場合のエラー."""


class RedirectTargetError(URLPolicyError):
    """redirect先が不正または欠落している場合のエラー."""


class UnsupportedSchemeError(URLPolicyError):
    """HTTP(S)以外のschemeが指定された場合のエラー."""


class HTTPStatusError(HTTPFetchError):
    """HTTP statusが要求された範囲外の場合のエラー."""

    def __init__(self, status: int, url: str):
        self.status = status
        self.url = url
        super().__init__(f"HTTP fetch failed: {status} for {url}")


class DNSResolver(Protocol):
    """DNS解決ポート.

    ``resolve`` はIP文字列のシーケンス、またはsocket.getaddrinfo互換のタプルを返せる。
    fake resolverを注入できるため、URLポリシーのテストは外部ネットワークを必要としない。
    """

    async def resolve(self, hostname: str) -> Sequence[str] | Sequence[Any]:
        """hostnameを全アドレスへ解決する."""


class HTTPResponse(Protocol):
    """HTTP transportから返される最小応答契約."""

    status: int
    headers: Mapping[str, str]

    async def iter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        """本文をchunk単位で返す."""

    async def close(self) -> None:
        """接続と応答を解放する."""


class HTTPTransport(Protocol):
    """HTTP requestの実装ポート.

    ``addresses`` はURLポリシーが検証したDNS結果である。標準transportはこの値を使って
    再解決を防ぎ、fake transportは無視してよい。
    """

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        addresses: Sequence[ipaddress._BaseAddress] = (),
    ) -> HTTPResponse:
        """redirectを追従せず、1回のHTTP requestを行う."""


class SourcePort(Protocol):
    """特定サイトsourceが満たす、HTTP実装非依存のport."""

    @classmethod
    def can_handle(cls, url: str) -> bool:
        """URLを担当できるか判定する."""

    async def fetch_chapter(self, url: str) -> Any:
        """指定URLから1章を取得する."""

    async def get_next_chapter_url(self, current_url: str) -> str | None:
        """次章URLを返す."""

    async def get_table_of_contents(self, work_url: str) -> list[Any]:
        """目次を返す."""


@dataclass(frozen=True)
class URLTarget:
    """検証済みURLと、その時点で解決されたIPアドレス."""

    url: str
    parsed: SplitResult
    hostname: str
    port: int
    addresses: tuple[ipaddress._BaseAddress, ...]


@dataclass(frozen=True)
class URLPolicy:
    """URL取得の安全設定.

    既定値は一般公開WebのHTTP(S)だけを対象とする。private networkへのアクセスを必要と
    する場合は ``allow_private_network=True`` または明示的な ``allowlist`` を指定する。
    どちらもデフォルトでは無効であり、環境変数や推測による緩和は行わない。
    """

    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    max_response_bytes: int = 10 * 1024 * 1024
    max_redirects: int = 5
    timeout_seconds: float = 30.0
    allowlist: frozenset[str] = frozenset()
    allow_private_network: bool = False
    resolver: DNSResolver | None = None

    def __post_init__(self) -> None:
        # dataclassの型を崩さず、list/setを受け取った場合も安全な不変値へ正規化する。
        schemes = (
            {self.allowed_schemes}
            if isinstance(self.allowed_schemes, str)
            else (self.allowed_schemes or ())
        )
        ports = (
            {self.allowed_ports}
            if isinstance(self.allowed_ports, int)
            else (self.allowed_ports or ())
        )
        allowlist = (
            {self.allowlist}
            if isinstance(self.allowlist, str)
            else (self.allowlist or ())
        )
        object.__setattr__(
            self,
            "allowed_schemes",
            frozenset(str(v).lower() for v in schemes),
        )
        object.__setattr__(self, "allowed_ports", frozenset(int(v) for v in ports))
        object.__setattr__(
            self,
            "allowlist",
            frozenset(_normalize_allowlist_entry(str(v)) for v in allowlist),
        )
        object.__setattr__(self, "allow_private_network", _coerce_bool(self.allow_private_network))
        object.__setattr__(self, "max_response_bytes", int(self.max_response_bytes))
        object.__setattr__(self, "max_redirects", int(self.max_redirects))
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if not self.allowed_schemes:
            raise ValueError("allowed_schemes must not be empty")
        if any(port < 1 or port > 65535 for port in self.allowed_ports):
            raise ValueError("allowed_ports must contain valid TCP ports")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @property
    def max_bytes(self) -> int:
        """旧名・簡易名からも最大応答サイズを参照できる."""

        return self.max_response_bytes

    @property
    def timeout(self) -> float:
        """旧名・簡易名からもtimeoutを参照できる."""

        return self.timeout_seconds

    def parse(self, url: str) -> tuple[SplitResult, str, int]:
        """DNS解決前のURL構文とauthorityを検証する."""

        if not isinstance(url, str) or not url:
            raise URLPolicyError("URL must be a non-empty string")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
            raise URLPolicyError("URL contains control characters")

        # fragmentはHTTP requestへ送られないため、検証とredirectの基準から除外する。
        request_url, _ = urldefrag(url)
        try:
            parsed = urlsplit(request_url)
        except ValueError as exc:
            raise URLPolicyError(f"Invalid URL: {url}") from exc

        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise UnsupportedSchemeError(
                f"Unsupported URL scheme: {parsed.scheme or '<empty>'}"
            )
        if scheme not in self.allowed_schemes:
            raise UnsupportedSchemeError(f"URL scheme is not allowed: {scheme}")
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise URLPolicyError("URL userinfo is not allowed")
        if not parsed.netloc:
            raise URLPolicyError("URL hostname is required")

        try:
            raw_hostname = parsed.hostname
            parsed_port = parsed.port
        except ValueError as exc:
            raise URLPolicyError("URL contains an invalid port or hostname") from exc
        if not raw_hostname:
            raise URLPolicyError("URL hostname is required")

        hostname = _normalize_hostname(raw_hostname)
        if not hostname:
            raise URLPolicyError("URL hostname is required")
        if "%" in hostname:
            raise URLPolicyError("IPv6 zone identifiers are not allowed")

        port = parsed_port if parsed_port is not None else (443 if scheme == "https" else 80)
        if port not in self.allowed_ports:
            raise URLPolicyError(f"URL port is not allowed: {port}")

        host_is_allowed = self._allowlist_matches_hostname(hostname)
        if _is_localhost_name(hostname) and not (host_is_allowed or self.allow_private_network):
            raise URLPolicyError(f"Localhost hostname is not allowed: {hostname}")

        # urlsplit accepts several malformed hostnames that socket.getaddrinfo would interpret
        # inconsistently. Validate labels before resolving; IDNA-normalized Unicode is allowed.
        if not _is_valid_hostname(hostname):
            raise URLPolicyError(f"Invalid URL hostname: {raw_hostname}")

        return parsed, hostname, port

    async def validate(self, url: str) -> URLTarget:
        """URLを構文検証し、DNS解決結果の全IPを検証して返す."""

        parsed, hostname, port = self.parse(url)
        try:
            addresses = (ipaddress.ip_address(hostname),)
        except ValueError:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    addresses = await _resolve_addresses(
                        self.resolver or SystemDNSResolver(), hostname
                    )
            except TimeoutError as exc:
                raise DNSResolutionError(f"DNS resolution timed out for {hostname}") from exc
        if not addresses:
            raise DNSResolutionError(f"Host did not resolve to an address: {hostname}")

        allowlisted = self._allowlist_matches_hostname(hostname)
        for address in addresses:
            if self._address_blocked(address, allowlisted=allowlisted):
                raise URLPolicyError(
                    f"URL resolves to a restricted network address: {hostname} ({address})"
                )

        # Return the fragment-free URL so each redirect is evaluated exactly as requested.
        request_url, _ = urldefrag(url)
        return URLTarget(request_url, parsed, hostname, port, tuple(addresses))

    async def check(self, url: str) -> URLTarget:
        """``validate``の短い別名."""

        return await self.validate(url)

    def _allowlist_matches_hostname(self, hostname: str) -> bool:
        for entry in self.allowlist:
            if _allowlist_host_matches(entry, hostname):
                return True
        return False

    def _allowlist_matches_address(self, address: ipaddress._BaseAddress) -> bool:
        for entry in self.allowlist:
            try:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _address_blocked(
        self,
        address: ipaddress._BaseAddress,
        *,
        allowlisted: bool,
    ) -> bool:
        # Multicast/reserved/unspecified endpoints are not useful content sources and remain
        # blocked even when a global private-network switch is enabled.
        if address.is_multicast or address.is_reserved or address.is_unspecified:
            return not (allowlisted and self._allowlist_matches_address(address))
        if self.allow_private_network or allowlisted or self._allowlist_matches_address(address):
            return False
        return bool(
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or not address.is_global
        )


class SystemDNSResolver:
    """``getaddrinfo``をイベントループ外のthreadで呼ぶ既定resolver."""

    async def resolve(self, hostname: str) -> tuple[str, ...]:
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                None,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise DNSResolutionError(f"DNS resolution failed for {hostname}") from exc
        values: list[str] = []
        for info in infos:
            try:
                value = info[4][0]
            except (IndexError, TypeError):
                continue
            if value not in values:
                values.append(value)
        return tuple(values)


async def _resolve_addresses(
    resolver: DNSResolver,
    hostname: str,
) -> tuple[ipaddress._BaseAddress, ...]:
    try:
        resolve = getattr(resolver, "resolve", None)
        if resolve is None:
            if not callable(resolver):
                raise TypeError("resolver must expose resolve() or be callable")
            resolve = resolver
        result = resolve(hostname)
        if inspect.isawaitable(result):
            result = await result
    except DNSResolutionError:
        raise
    except (OSError, socket.gaierror) as exc:
        raise DNSResolutionError(f"DNS resolution failed for {hostname}") from exc
    except Exception as exc:
        raise DNSResolutionError(f"DNS resolution failed for {hostname}") from exc

    values: list[ipaddress._BaseAddress] = []
    for item in result or ():
        candidate: Any = item
        if isinstance(item, tuple):
            # getaddrinfo result or sockaddr tuple.
            if len(item) >= 5 and isinstance(item[4], tuple):
                candidate = item[4][0]
            elif item and isinstance(item[0], str):
                candidate = item[0]
        if isinstance(candidate, bytes):
            candidate = candidate.decode("ascii", errors="ignore")
        try:
            address = ipaddress.ip_address(str(candidate).split("%", 1)[0])
        except ValueError:
            continue
        if address not in values:
            values.append(address)
    return tuple(values)


def _normalize_hostname(hostname: str) -> str:
    value = hostname.strip().rstrip(".").lower()
    if not value:
        return ""
    try:
        # IPv6 literals are already ASCII, while IDNA gives stable allowlist matching for Unicode.
        if ":" in value:
            return value
        return value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise URLPolicyError(f"Invalid URL hostname: {hostname}") from exc


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_allowlist_entry(entry: str) -> str:
    value = entry.strip().lower()
    if not value:
        raise ValueError("allowlist entries must not be empty")
    if "/" in value:
        try:
            return str(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(f"Invalid allowlist network: {entry}") from exc
    if value.startswith("*.") or value.startswith("."):
        suffix = value[2:] if value.startswith("*.") else value[1:]
        return f"*.{_normalize_hostname(suffix)}"
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return _normalize_hostname(value.lstrip("."))


def _allowlist_host_matches(entry: str, hostname: str) -> bool:
    if "/" in entry:
        return False
    if entry.startswith("*."):
        suffix = entry[2:]
        return hostname == suffix or hostname.endswith(f".{suffix}")
    try:
        return ipaddress.ip_address(hostname) == ipaddress.ip_address(entry)
    except ValueError:
        pass
    return hostname == entry


def _is_localhost_name(hostname: str) -> bool:
    return hostname == "localhost" or hostname.endswith(".localhost") or hostname in {
        "localhost.localdomain",
        "localhost4",
        "localhost4.localdomain",
        "localhost6",
        "localhost6.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }


_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def _is_valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    if len(hostname) > 253 or hostname.startswith(".") or hostname.endswith("."):
        return False
    return all(_HOST_LABEL_RE.fullmatch(label) for label in hostname.split("."))


__all__ = [
    "DNSResolutionError",
    "DNSResolver",
    "HTTPFetchError",
    "HTTPResponse",
    "HTTPStatusError",
    "HTTPTransport",
    "RedirectLimitError",
    "RedirectTargetError",
    "ResponseTooLargeError",
    "SourcePort",
    "SystemDNSResolver",
    "URLPolicy",
    "URLPolicyError",
    "URLTarget",
    "UnsupportedSchemeError",
]
