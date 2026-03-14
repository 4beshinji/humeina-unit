"""Tests for content sources."""

from yomiage.sources.aozora import AozoraSource
from yomiage.sources.registry import resolve


def test_aozora_can_handle():
    assert AozoraSource.can_handle("https://www.aozora.gr.jp/cards/000148/files/773_14560.html")
    assert not AozoraSource.can_handle("https://ncode.syosetu.com/n1234ab/")


def test_registry_resolve_aozora():
    source = resolve("https://www.aozora.gr.jp/cards/000148/files/773_14560.html")
    assert isinstance(source, AozoraSource)


def test_registry_resolve_unknown():
    import pytest

    with pytest.raises(ValueError, match="No content source"):
        resolve("https://example.com/unknown")
