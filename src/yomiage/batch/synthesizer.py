"""Phase B: Common synthesizer interface for batch pipeline."""

from abc import ABC, abstractmethod
from pathlib import Path

from .manifest import SentenceEntry


class BatchSynthesizer(ABC):
    """バッチ合成の共通インターフェース."""

    @abstractmethod
    async def synthesize_sentence(
        self, entry: SentenceEntry, output_dir: Path
    ) -> str | None:
        """1文を合成してWAVファイルを出力.

        Returns: 出力ファイル名（例: "0001.wav"）、失敗時は None
        """
        ...

    @abstractmethod
    async def generate_silence(
        self, duration: float, output_path: Path
    ) -> None:
        """無音WAVを生成."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """プロバイダーが利用可能か."""
        ...
