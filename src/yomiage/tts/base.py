"""TTS Provider abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def healthy(self) -> bool:
        return True

    @abstractmethod
    async def synthesize(
        self, text: str, voice: str = "neutral", speed: float = 1.0, **params
    ) -> AudioResult: ...

    @abstractmethod
    async def is_available(self) -> bool: ...

    async def list_voices(self) -> list[dict]:
        """利用可能なボイス一覧を返す."""
        return []
