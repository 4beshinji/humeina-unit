"""Phase B: Common synthesizer interface for batch pipeline."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..application.synthesis import SynthesisRequest, SynthesisService
from ..core.models import VoiceIntent
from .manifest import SentenceEntry


class BatchSynthesizer(ABC):
    """バッチ合成の共通インターフェース。

    Providerのretry/fallback/cacheは ``SynthesisService`` に集約する。旧Providerを直接
    渡す呼び出しは、ここで一度だけcanonical serviceへ適合して互換利用できる。
    """

    provider: Any | None = None
    synthesis_service: SynthesisService | None = None
    _owns_synthesis_service: bool = False

    def _ensure_synthesis_service(self) -> SynthesisService:
        if self.synthesis_service is None:
            provider = self.provider
            if provider is None:
                raise RuntimeError("batch synthesizer requires a provider from bootstrap")
            from ..bootstrap import create_synthesis_service

            self.synthesis_service = create_synthesis_service(
                {str(provider.name): provider},
                primary=str(provider.name),
            )
            self._owns_synthesis_service = True
        return self.synthesis_service

    async def _synthesize_request(
        self,
        *,
        text: str,
        destination: Path,
        intent: VoiceIntent,
        metadata: dict[str, Any],
    ) -> Any:
        return await self._ensure_synthesis_service().synthesize(
            SynthesisRequest(
                text=text,
                voice_intent=intent,
                destination=str(destination),
                metadata=metadata,
            )
        )

    async def close(self) -> None:
        """Close only a service created by this compatibility facade."""

        if self._owns_synthesis_service and self.synthesis_service is not None:
            await self.synthesis_service.close()
            self.synthesis_service = None
            self._owns_synthesis_service = False

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
