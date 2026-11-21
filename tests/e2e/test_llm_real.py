"""明示opt-in時だけ実行する実LLM smoke test。"""

from __future__ import annotations

import json
import os

import pytest

from yomiage.llm.models import LLMRequest
from yomiage.llm.transports import (
    ChatCompletionsTransport,
    MessagesTransport,
    ResponsesTransport,
)
from yomiage.nlp.llm_utils import extract_json

pytestmark = pytest.mark.e2e


@pytest.mark.paid_llm
@pytest.mark.parametrize(
    ("transport_type", "model"),
    [
        (ChatCompletionsTransport, "glm-5.3-flash"),
        (ResponsesTransport, "gpt-5.6-luna"),
        (MessagesTransport, "qwen3.8-flash"),
    ],
)
async def test_opencode_go_protocol_smoke(transport_type, model: str) -> None:
    if os.getenv("RUN_PAID_LLM_TESTS") != "1":
        pytest.skip("set RUN_PAID_LLM_TESTS=1 to permit paid OpenCode Go requests")
    api_key = os.getenv("OPENCODE_GO_API_KEY")
    if not api_key:
        pytest.skip("OPENCODE_GO_API_KEY is not configured")
    transport = transport_type(
        provider="opencode_go",
        model=model,
        base_url="https://opencode.ai/zen/go/v1",
        api_key=api_key,
    )
    try:
        response = await transport.complete(
            LLMRequest(task="e2e", prompt="Reply with OK only.", max_output_tokens=512)
        )
    finally:
        await transport.close()

    assert response.text.strip()
    assert response.model == model


@pytest.mark.parametrize("sentence_count", [25, 50, 100])
async def test_llama_cpp_json_completeness(sentence_count: int) -> None:
    """実機opt-in時に、全入力IDを構造化出力で欠落なく返せることを測る。"""

    if os.getenv("RUN_LLAMA_CPP_QUALITY_E2E") != "1":
        pytest.skip("set RUN_LLAMA_CPP_QUALITY_E2E=1 to run the quality fixture")
    if not os.getenv("YOMIAGE_LLM_GPU_DEVICE"):
        pytest.fail("YOMIAGE_LLM_GPU_DEVICE must explicitly select the ROCm GPU")
    base_url = os.getenv("LLAMA_CPP_URL", "http://127.0.0.1:8080/v1")
    model = os.getenv("LLAMA_CPP_MODEL", "Qwen3.8-27B")
    transport = ChatCompletionsTransport(
        provider="local", model=model, base_url=base_url, timeout_seconds=300
    )
    items = [{"id": index, "text": f"文{index}を読み上げる。"} for index in range(sentence_count)]
    try:
        response = await transport.complete(
            LLMRequest(
                task="quality_e2e",
                prompt=(
                    "各要素を読み、idとkind='narration'だけを持つJSON配列を入力順に返してください。"
                    "\n" + json.dumps(items, ensure_ascii=False)
                ),
                response_mode="json",
                json_schema={
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "kind": {"type": "string", "const": "narration"},
                        },
                        "required": ["id", "kind"],
                        "additionalProperties": False,
                    },
                },
                max_output_tokens=sentence_count * 24 + 128,
            )
        )
    finally:
        await transport.close()

    payload = extract_json(response.text)
    assert isinstance(payload, list)
    assert [item.get("id") for item in payload] == list(range(sentence_count))
    assert response.model == model


@pytest.mark.parametrize(
    "transport_type",
    [ChatCompletionsTransport, ResponsesTransport, MessagesTransport],
)
async def test_llama_cpp_protocol_smoke(transport_type) -> None:
    if os.getenv("RUN_LLAMA_CPP_E2E") != "1":
        pytest.skip("set RUN_LLAMA_CPP_E2E=1 after selecting the ROCm GPU explicitly")
    if not os.getenv("YOMIAGE_LLM_GPU_DEVICE"):
        pytest.fail("YOMIAGE_LLM_GPU_DEVICE must explicitly select the ROCm GPU")
    base_url = os.getenv("LLAMA_CPP_URL", "http://127.0.0.1:8080/v1")
    model = os.getenv("LLAMA_CPP_MODEL", "Qwen3.8-27B")
    transport = transport_type(provider="local", model=model, base_url=base_url)
    try:
        response = await transport.complete(
            LLMRequest(task="e2e", prompt="OKとだけ答えてください。", max_output_tokens=8)
        )
    finally:
        await transport.close()

    assert response.text.strip()
