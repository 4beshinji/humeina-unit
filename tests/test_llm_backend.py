"""Tests for LLM backend abstraction."""

import pytest

from yomiage.nlp.llm_backend import (
    AnthropicBackend,
    LLMBackend,
    OllamaBackend,
    OpenAIBackend,
    create_llm_backend,
)
from yomiage.nlp.llm_utils import extract_json


class TestExtractJson:
    def test_plain_json_array(self):
        assert extract_json('[{"a": 1}]') == [{"a": 1}]

    def test_plain_json_object(self):
        assert extract_json('{"key": "value"}') == {"key": "value"}

    def test_json_in_code_block(self):
        text = '```json\n[{"x": 1}]\n```'
        assert extract_json(text) == [{"x": 1}]

    def test_json_in_generic_code_block(self):
        text = '```\n{"y": 2}\n```'
        assert extract_json(text) == {"y": 2}

    def test_text_before_json(self):
        text = 'Here is the result:\n[{"z": 3}]'
        assert extract_json(text) == [{"z": 3}]

    def test_invalid_json_returns_empty_dict(self):
        assert extract_json("not json at all") == {}

    def test_empty_string_returns_empty_dict(self):
        assert extract_json("") == {}


class TestCreateLlmBackend:
    def test_create_ollama(self):
        backend = create_llm_backend("ollama")
        assert isinstance(backend, OllamaBackend)

    def test_create_ollama_custom_url(self):
        backend = create_llm_backend(
            "ollama", url="http://custom:11434", model="llama3"
        )
        assert isinstance(backend, OllamaBackend)
        assert backend._client.url == "http://custom:11434"
        assert backend._client.model == "llama3"

    def test_create_openai(self):
        backend = create_llm_backend("openai", api_key="test-key")
        assert isinstance(backend, OpenAIBackend)

    def test_create_openai_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            create_llm_backend("openai")

    def test_create_anthropic(self):
        backend = create_llm_backend("anthropic", api_key="test-key")
        assert isinstance(backend, AnthropicBackend)

    def test_create_anthropic_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            create_llm_backend("anthropic")

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_llm_backend("unknown_backend")


class TestLLMBackendInterface:
    def test_ollama_backend_is_llm_backend(self):
        backend = OllamaBackend()
        assert isinstance(backend, LLMBackend)

    def test_openai_backend_is_llm_backend(self):
        backend = OpenAIBackend(api_key="test")
        assert isinstance(backend, LLMBackend)

    def test_anthropic_backend_is_llm_backend(self):
        backend = AnthropicBackend(api_key="test")
        assert isinstance(backend, LLMBackend)

    def test_ollama_client_property(self):
        backend = OllamaBackend(url="http://test:11434", model="test-model")
        assert backend.client.url == "http://test:11434"
        assert backend.client.model == "test-model"
