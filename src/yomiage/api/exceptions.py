"""SDK 公開用の例外階層."""

from __future__ import annotations


class YomiageError(Exception):
    """humeina-unit SDK の基底例外."""

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class TTSError(YomiageError):
    """TTS 合成に関する基底例外."""


class ProviderUnavailableError(TTSError):
    """TTS プロバイダーに到達できない場合."""


class SynthesisError(TTSError):
    """プロバイダーは応答したが合成に失敗した場合."""


class AuthenticationError(TTSError):
    """認証・認可に失敗した場合."""


class RateLimitError(TTSError):
    """レートリミットに到達した場合."""


class TimeoutError(TTSError):
    """タイムアウトした場合."""


class ConfigError(YomiageError):
    """設定に関する例外."""


class ValidationError(YomiageError):
    """入力値検証に関する例外."""


def map_aiohttp_error(
    engine: str,
    status: int | None,
    message: str,
    *,
    source: Exception | None = None,
) -> TTSError:
    """aiohttp レスポンスエラーを SDK 例外にマッピング."""
    details = {"engine": engine, "status": status}
    if status == 401 or status == 403:
        return AuthenticationError(message, details=details)
    if status == 429:
        return RateLimitError(message, details=details)
    if status is not None and 500 <= status < 600:
        return SynthesisError(message, details=details)
    if status is not None:
        return SynthesisError(message, details=details)
    return ProviderUnavailableError(message, details=details)


def map_client_error(engine: str, exc: Exception) -> TTSError:
    """aiohttp クライアントエラー（接続・タイムアウト等）を SDK 例外にマッピング."""
    import asyncio

    message = f"{engine} request failed: {exc}"
    if isinstance(exc, asyncio.TimeoutError):
        return TimeoutError(message, details={"engine": engine})
    return ProviderUnavailableError(message, details={"engine": engine})
