"""Ollama API client for SLM inference."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
from loguru import logger

from .llm_utils import ROMANIZE_SYSTEM_PROMPT, extract_json

if TYPE_CHECKING:
    from .gemini_client import GeminiClient


class OllamaClient:
    """Ollama REST API クライアント."""

    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "qwen3.5:3b",
        fallback: GeminiClient | None = None,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.fallback = fallback

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """テキスト生成."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            ) as session:
                async with session.post(f"{self.url}/api/chat", json=body) as resp:
                    if resp.status != 200:
                        detail = await resp.text()
                        raise RuntimeError(f"Ollama chat failed: {resp.status} {detail}")
                    result = await resp.json()
                    return result.get("message", {}).get("content", "")
        except Exception as e:
            if self.fallback:
                logger.warning(f"Ollama failed ({e}), falling back to Gemini")
                return await self.fallback.generate(prompt, system, temperature, max_tokens)
            raise

    async def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> list | dict:
        """JSON出力を生成・パース."""
        response = await self.generate(prompt, system, temperature)
        return extract_json(response)

    async def romanize(self, text: str) -> str:
        """テキスト中のアルファベットを日本語の読み仮名に変換."""
        if not await self.is_available():
            return text
        try:
            return await self.generate(
                text,
                system=ROMANIZE_SYSTEM_PROMPT,
                temperature=0.1,
            )
        except Exception as e:
            logger.warning(f"Romanization failed: {e}")
            return text

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(f"{self.url}/api/tags") as resp:
                    if resp.status == 200:
                        return True
        except Exception:
            pass
        if self.fallback:
            return await self.fallback.is_available()
        return False
