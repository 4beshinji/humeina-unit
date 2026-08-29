"""LLM Transportと互換Backendの契約。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from loguru import logger

from ..nlp.llm_utils import ROMANIZE_SYSTEM_PROMPT, extract_json
from .models import LLMRequest, LLMResponse


class LLMProtocol(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    MESSAGES = "messages"
    OLLAMA_CHAT = "ollama_chat"
    GEMINI = "gemini"


class LLMTransport(ABC):
    """HTTP API形式ごとの最小契約。"""

    provider: str
    model: str
    protocol: LLMProtocol

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """一回の物理リクエストを実行する。"""

    @abstractmethod
    async def health(self) -> bool:
        """接続先が応答可能か確認する。"""

    async def count_tokens(self, text: str) -> int | None:
        """Provider側count endpointがなければNoneを返す。"""

        return None

    async def close(self) -> None:
        """Transportが所有する資源を閉じる。"""


class LLMBackend(ABC):
    """既存feature APIを保つ高水準Backend契約。"""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        task: str = "generic",
    ) -> str:
        """テキストを生成する。"""

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
        task: str = "generic",
        json_schema: dict | None = None,
        max_tokens: int = 2048,
    ) -> list | dict:
        """JSON生成結果をパースする。"""

        response = await self.generate(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            task=task,
        )
        return extract_json(response)

    @abstractmethod
    async def is_available(self) -> bool:
        """TTL付きの可用性状態を返す。"""

    async def romanize(self, text: str) -> str:
        """英字語句を読み仮名へ変換する。"""

        try:
            return await self.generate(
                text,
                system=ROMANIZE_SYSTEM_PROMPT,
                temperature=0.1,
                task="romanization",
            )
        except Exception as exc:
            logger.warning(f"Romanization failed: {exc}")
            return text

    async def romanize_many(self, texts: list[str]) -> list[str]:
        """複数文を集約してローマ字化する既定実装。"""

        return [await self.romanize(text) for text in texts]

    async def close(self) -> None:
        """所有資源を閉じる。"""
