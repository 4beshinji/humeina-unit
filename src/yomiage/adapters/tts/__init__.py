"""旧 ``yomiage.tts`` Provider を新しいportへ適合するadapter。"""

from .legacy import LegacyTTSProviderAdapter, VoisonaFileProviderAdapter, adapt_provider

__all__ = ["LegacyTTSProviderAdapter", "VoisonaFileProviderAdapter", "adapt_provider"]
