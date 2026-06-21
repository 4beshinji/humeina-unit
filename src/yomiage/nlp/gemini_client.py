"""Google Gemini API client — fallback for Ollama."""

import aiohttp
from loguru import logger

from .llm_utils import ROMANIZE_SYSTEM_PROMPT, extract_json

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiClient:
    """Gemini REST API クライアント（OllamaClient互換インターフェース）."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """テキスト生成."""
        url = f"{GEMINI_API_BASE}/models/{self.model}:generateContent?key={self.api_key}"

        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.post(url, json=body) as resp:
                if resp.status != 200:
                    detail = await resp.text()
                    raise RuntimeError(f"Gemini API failed: {resp.status} {detail}")
                result = await resp.json()
                candidates = result.get("candidates", [])
                if not candidates:
                    return ""
                parts = candidates[0].get("content", {}).get("parts", [])
                return parts[0].get("text", "") if parts else ""

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
        """APIキーが設定されていればTrue."""
        return bool(self.api_key)
