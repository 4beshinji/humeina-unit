"""Content source registry — auto-detects source from URL."""

from loguru import logger

from .aozora import AozoraSource
from .base import ContentSource
from .generic_web import GenericWebSource
from .kakuyomu import KakuyomuSource
from .narou import NarouSource

_SOURCES: list[type[ContentSource]] = [
    AozoraSource,
    NarouSource,
    KakuyomuSource,
    GenericWebSource,  # フォールバック — 必ず最後
]


def register_source(source_cls: type[ContentSource]) -> None:
    """Register a content source class."""
    if source_cls not in _SOURCES:
        _SOURCES.append(source_cls)


def resolve(url: str) -> ContentSource:
    """Resolve a URL to a content source instance."""
    for source_cls in _SOURCES:
        if source_cls.can_handle(url):
            logger.debug(f"Resolved {url} → {source_cls.__name__}")
            return source_cls()
    raise ValueError(f"No content source can handle URL: {url}")
