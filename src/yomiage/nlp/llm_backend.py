"""LLM backend abstraction for text analysis."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import aiohttp
from loguru import logger

from .ollama_client import OllamaClient


class LLMBackend(ABC):
    """LLM分析バックエンドの抽象基底クラス."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """テキスト生成."""
        ...

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> list | dict:
        """JSON出力を生成・パース."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """バックエンドが利用可能か確認."""
        ...

    async def romanize(self, text: str) -> str:
        """テキスト中のアルファベットを日本語の読み仮名に変換.

        デフォルト実装は generate() を使用。
        """
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


class OllamaBackend(LLMBackend):
    """既存OllamaClientをラップするバックエンド."""

    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "qwen3.5:3b",
    ):
        self._client = OllamaClient(url=url, model=model)

    @property
    def client(self) -> OllamaClient:
        """内部OllamaClientへのアクセス（後方互換用）."""
        return self._client

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        return await self._client.generate(
            prompt, system=system, temperature=temperature, max_tokens=max_tokens
        )

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> list | dict:
        return await self._client.generate_json(
            prompt, system=system, temperature=temperature
        )

    async def is_available(self) -> bool:
        return await self._client.is_available()

    async def romanize(self, text: str) -> str:
        return await self._client.romanize(text)


class OpenAIBackend(LLMBackend):
    """OpenAI互換API（OpenAI, vLLM, LM Studio等）バックエンド."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.post(
                f"{self._base_url}/chat/completions", json=body, headers=headers
            ) as resp:
                if resp.status != 200:
                    detail = await resp.text()
                    raise RuntimeError(f"OpenAI API failed: {resp.status} {detail}")
                result = await resp.json()
                return result["choices"][0]["message"]["content"]

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> list | dict:
        response = await self.generate(
            prompt, system=system, temperature=temperature
        )
        return _extract_json(response)

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                headers = {"Authorization": f"Bearer {self._api_key}"}
                async with session.get(
                    f"{self._base_url}/models", headers=headers
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False


class AnthropicBackend(LLMBackend):
    """Anthropic Claude APIバックエンド."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
    ):
        self._api_key = api_key
        self._model = model

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        body: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if system:
            body["system"] = system

        headers = {
            "x-api-key": self._api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages", json=body, headers=headers
            ) as resp:
                if resp.status != 200:
                    detail = await resp.text()
                    raise RuntimeError(f"Anthropic API failed: {resp.status} {detail}")
                result = await resp.json()
                return result["content"][0]["text"]

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> list | dict:
        response = await self.generate(
            prompt, system=system, temperature=temperature
        )
        return _extract_json(response)

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                headers = {
                    "x-api-key": self._api_key,
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                }
                body = {
                    "model": self._model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                }
                async with session.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    headers=headers,
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False


def create_llm_backend(
    backend: str = "ollama",
    *,
    url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    **kwargs: object,
) -> LLMBackend:
    """バックエンド名からLLMBackendインスタンスを生成するファクトリ."""
    if backend == "ollama":
        return OllamaBackend(
            url=url or "http://localhost:11434",
            model=model or "qwen3.5:3b",
        )
    elif backend == "openai":
        if not api_key:
            raise ValueError("OpenAI backend requires api_key")
        return OpenAIBackend(
            api_key=api_key,
            model=model or "gpt-4o-mini",
            base_url=url or "https://api.openai.com/v1",
        )
    elif backend == "anthropic":
        if not api_key:
            raise ValueError("Anthropic backend requires api_key")
        return AnthropicBackend(
            api_key=api_key,
            model=model or "claude-sonnet-4-20250514",
        )
    else:
        raise ValueError(f"Unknown LLM backend: {backend}")


def _extract_json(response: str) -> list | dict:
    """LLMレスポンスからJSONを抽出."""
    text = response.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    for i, c in enumerate(text):
        if c in ("[", "{"):
            text = text[i:]
            break

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON from LLM: {e}\nResponse: {response[:200]}")
        return {}
