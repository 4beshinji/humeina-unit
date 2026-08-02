"""SEC-002 URL policy and common fetcher tests (external network free)."""

from __future__ import annotations

import asyncio
import ipaddress

import pytest

from yomiage.ports.sources import (
    DNSResolutionError,
    RedirectLimitError,
    ResponseTooLargeError,
    UnsupportedSchemeError,
    URLPolicy,
    URLPolicyError,
)
from yomiage.sources.generic_web import GenericWebSource
from yomiage.sources.http import HTTPFetcher


class FakeResolver:
    def __init__(self, addresses: dict[str, list[str] | str]):
        self.addresses = addresses
        self.calls: list[str] = []

    async def resolve(self, hostname: str):
        self.calls.append(hostname)
        value = self.addresses[hostname]
        if isinstance(value, str):
            return [value]
        return value


class RebindingResolver:
    def __init__(self, answers: list[str]):
        self.answers = answers
        self.calls = 0

    async def resolve(self, _hostname: str):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return [answer]


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    async def iter_bytes(self, chunk_size: int):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    async def close(self):
        return None


class FakeTransport:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, url: str, **_kwargs):
        self.calls.append((method, url))
        return self.responses[url]


def _policy(resolver, **kwargs):
    return URLPolicy(resolver=resolver, **kwargs)


@pytest.mark.asyncio
async def test_private_and_special_addresses_are_rejected_by_default():
    resolver = FakeResolver(
        {
            "loopback.example": "127.0.0.1",
            "ipv6-loopback.example": "::1",
            "private.example": "192.168.10.10",
            "linklocal.example": "169.254.10.10",
            "multicast.example": "224.0.0.1",
            "public.example": "93.184.216.34",
        }
    )
    policy = _policy(resolver)

    for hostname in (
        "loopback.example",
        "ipv6-loopback.example",
        "private.example",
        "linklocal.example",
        "multicast.example",
    ):
        with pytest.raises(URLPolicyError):
            await policy.validate(f"https://{hostname}/")

    target = await policy.validate("https://public.example/")
    assert target.addresses == (ipaddress.ip_address("93.184.216.34"),)


@pytest.mark.asyncio
async def test_private_network_requires_explicit_setting_or_allowlist():
    resolver = FakeResolver({"local.example": "127.0.0.1"})
    with pytest.raises(URLPolicyError):
        await _policy(resolver).validate("http://local.example/")

    target = await _policy(resolver, allow_private_network=True).validate(
        "http://local.example/"
    )
    assert target.hostname == "local.example"

    target = await _policy(resolver, allowlist={"local.example"}).validate(
        "http://local.example/"
    )
    assert target.addresses == (ipaddress.ip_address("127.0.0.1"),)


@pytest.mark.asyncio
async def test_scheme_userinfo_port_and_localhost_name_are_checked():
    resolver = FakeResolver({"public.example": "93.184.216.34"})
    policy = _policy(resolver)
    with pytest.raises(UnsupportedSchemeError):
        await policy.validate("file://public.example/tmp")
    with pytest.raises(UnsupportedSchemeError):
        await _policy(resolver, allowed_schemes={"file"}).validate("file://public.example/tmp")
    with pytest.raises(URLPolicyError):
        await policy.validate("https://user:password@public.example/")
    with pytest.raises(URLPolicyError):
        await policy.validate("https://public.example:8443/")
    with pytest.raises(URLPolicyError):
        await policy.validate("http://localhost/")


@pytest.mark.asyncio
async def test_dns_rebinding_is_checked_for_each_redirect():
    resolver = RebindingResolver(["93.184.216.34", "127.0.0.1"])
    transport = FakeTransport(
        {
            "https://public.example/": FakeResponse(
                302,
                headers={"Location": "https://public.example/private"},
            )
        }
    )
    fetcher = HTTPFetcher(_policy(resolver), transport=transport)

    with pytest.raises(URLPolicyError):
        await fetcher.fetch("https://public.example/")
    assert resolver.calls == 2
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_redirect_destination_is_revalidated_and_redirects_are_bounded():
    resolver = FakeResolver(
        {"public.example": "93.184.216.34", "private.example": "192.168.1.2"}
    )
    transport = FakeTransport(
        {
            "https://public.example/": FakeResponse(
                302,
                headers={"Location": "https://private.example/"},
            )
        }
    )
    with pytest.raises(URLPolicyError):
        await HTTPFetcher(_policy(resolver), transport=transport).fetch(
            "https://public.example/"
        )

    loop_resolver = FakeResolver({"loop.example": "93.184.216.34"})
    loop_transport = LoopTransport()
    fetcher = HTTPFetcher(
        _policy(loop_resolver, max_redirects=1),
        transport=loop_transport,
    )
    with pytest.raises(RedirectLimitError):
        await fetcher.fetch("https://loop.example/")
    assert len(loop_transport.calls) == 2


class LoopTransport:
    def __init__(self):
        self.calls: list[str] = []

    async def request(self, _method: str, url: str, **_kwargs):
        self.calls.append(url)
        return FakeResponse(302, headers={"Location": url})


@pytest.mark.asyncio
async def test_response_size_is_checked_before_and_during_streaming():
    resolver = FakeResolver({"public.example": "93.184.216.34"})
    declared = FakeTransport(
        {
            "https://public.example/": FakeResponse(
                200,
                body=b"x",
                headers={"Content-Length": "11"},
            )
        }
    )
    with pytest.raises(ResponseTooLargeError):
        await HTTPFetcher(_policy(resolver, max_response_bytes=10), transport=declared).fetch(
            "https://public.example/"
        )

    streamed = FakeTransport(
        {"https://public.example/": FakeResponse(200, body=b"0123456789abcdef")}
    )
    with pytest.raises(ResponseTooLargeError):
        await HTTPFetcher(_policy(resolver, max_response_bytes=10), transport=streamed).fetch(
            "https://public.example/"
        )


@pytest.mark.asyncio
async def test_timeout_is_enforced_without_network():
    class SlowTransport:
        async def request(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    resolver = FakeResolver({"public.example": "93.184.216.34"})
    fetcher = HTTPFetcher(
        _policy(resolver, timeout_seconds=0.01), transport=SlowTransport()
    )
    with pytest.raises(Exception, match="timed out"):
        await fetcher.fetch("https://public.example/")


@pytest.mark.asyncio
async def test_dns_failure_is_explicit():
    class FailingResolver:
        async def resolve(self, _hostname):
            raise OSError("no DNS")

    with pytest.raises(DNSResolutionError):
        await _policy(FailingResolver()).validate("https://public.example/")


@pytest.mark.asyncio
async def test_generic_source_uses_the_common_fetcher():
    resolver = FakeResolver({"public.example": "93.184.216.34"})
    transport = FakeTransport(
        {
            "https://public.example/": FakeResponse(
                200,
                body=(
                    b"<html><head><title>Title</title></head><body><article>"
                    b"This is a sufficiently long article body for the extraction fallback."
                    b"</article></body></html>"
                ),
            )
        }
    )
    source = GenericWebSource(HTTPFetcher(_policy(resolver), transport=transport))

    chapter = await source.fetch_chapter("https://public.example/")

    assert chapter.title == "Title"
    assert "sufficiently long article" in chapter.text
    assert transport.calls == [("GET", "https://public.example/")]
