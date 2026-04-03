"""Tests for content sources."""

from yomiage.sources.aozora import AozoraSource
from yomiage.sources.generic_web import GenericWebSource
from yomiage.sources.narou import NarouSource
from yomiage.sources.registry import resolve


def test_aozora_can_handle():
    assert AozoraSource.can_handle("https://www.aozora.gr.jp/cards/000148/files/773_14560.html")
    assert not AozoraSource.can_handle("https://ncode.syosetu.com/n1234ab/")


def test_registry_resolve_aozora():
    source = resolve("https://www.aozora.gr.jp/cards/000148/files/773_14560.html")
    assert isinstance(source, AozoraSource)


def test_registry_resolve_generic_fallback():
    source = resolve("https://example.com/unknown")
    assert isinstance(source, GenericWebSource)


def test_generic_web_does_not_override_specific():
    source = resolve("https://www.aozora.gr.jp/cards/000148/files/773_14560.html")
    assert isinstance(source, AozoraSource)

    source = resolve("https://ncode.syosetu.com/n1234ab/")
    assert isinstance(source, NarouSource)
