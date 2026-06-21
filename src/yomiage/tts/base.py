"""TTS Provider abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp


@dataclass
class AudioResult:
    """TTS合成結果."""

    audio_data: bytes
    format: str = "wav"
    sample_rate: int | None = None
    duration: float | None = None


@dataclass
class TTSParams:
    """TTS合成パラメータ."""

    voice_id: str | None = None
    speed: float = 1.0
    pitch: float = 0.0
    volume: float = 0.0
    intonation: float = 1.0
    huskiness: float = 0.0
    alp: float = 0.0
    style_weights: list[float] | None = None
    extra: dict = field(default_factory=dict)


class TTSProvider(ABC):
    """TTSプロバイダー抽象基底クラス."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._session_external: bool = False

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def healthy(self) -> bool:
        return True

    @property
    def is_slow(self) -> bool:
        """低速プロバイダーはパイプライン合成戦略を使用."""
        return False

    def set_session(self, session: aiohttp.ClientSession | None) -> None:
        """外部から aiohttp.ClientSession を注入."""
        self._session = session
        self._session_external = session is not None

    @property
    def session(self) -> aiohttp.ClientSession:
        """共有セッションを返す. 未設定ならデフォルトマネージャーから取得."""
        if self._session is not None and not self._session.closed:
            return self._session
        from .session import get_default_session_manager

        self._session = get_default_session_manager().acquire()
        self._session_external = False
        return self._session

    async def close(self) -> None:
        """プロバイダーが保持する外部リソースを解放."""
        if self._session is None or self._session.closed:
            self._session = None
            return
        if self._session_external:
            # 外部注入セッションは閉じずに参照を外すだけ
            self._session = None
            self._session_external = False
        else:
            from .session import get_default_session_manager

            await get_default_session_manager().release()
            self._session = None

    @abstractmethod
    async def synthesize(
        self, text: str, voice: str = "neutral", speed: float = 1.0, **params
    ) -> AudioResult: ...

    @abstractmethod
    async def is_available(self) -> bool: ...

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str,
        voice: str = "neutral",
        speed: float = 1.0,
        **params,
    ) -> AudioResult:
        """ファイルに直接合成出力. デフォルトはsynthesize+書き込み."""
        result = await self.synthesize(text, voice, speed, **params)
        if result.audio_data:
            Path(output_path).write_bytes(result.audio_data)
        return result

    async def list_voices(self) -> list[dict]:
        """利用可能なボイス一覧を返す."""
        return []
