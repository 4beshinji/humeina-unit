"""Ollama API client for SLM inference."""

import json

import aiohttp
from loguru import logger


class OllamaClient:
    """Ollama REST API クライアント."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "qwen3.5:3b"):
        self.url = url.rstrip("/")
        self.model = model

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

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.post(f"{self.url}/api/chat", json=body) as resp:
                if resp.status != 200:
                    detail = await resp.text()
                    raise RuntimeError(f"Ollama chat failed: {resp.status} {detail}")
                result = await resp.json()
                return result.get("message", {}).get("content", "")

    async def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> list | dict:
        """JSON出力を生成・パース."""
        response = await self.generate(prompt, system, temperature)

        # JSON部分を抽出（コードブロック内の場合も対応）
        text = response.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]

        # [ または { から始まる部分を探す
        for i, c in enumerate(text):
            if c in ("[", "{"):
                text = text[i:]
                break

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from Ollama: {e}\nResponse: {response[:200]}")
            return {}

    async def romanize(self, text: str) -> str:
        """テキスト中のアルファベットを日本語の読み仮名に変換."""
        if not await self.is_available():
            return text
        try:
            return await self.generate(
                text,
                system=(
                    "あなたはテキスト変換ツールです。"
                    "テキスト中のアルファベット（英単語・略語・固有名詞）を"
                    "すべて正しい日本語の読み（カタカナ）に書き換えてください。"
                    "略語も必ずカタカナにしてください"
                    "（例: BBC→ビービーシー, AI→エーアイ, UK→ユーケー）。"
                    "アルファベットが一文字も残らないようにしてください。"
                    "それ以外の部分は一切変更しないでください。変換後のテキストのみ出力してください。"
                ),
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
                    return resp.status == 200
        except Exception:
            return False
