"""Shared provider port contract suite.

Every adapter shape must expose identical health, voice, synthesis, file, timeout,
cancellation, and lifecycle behavior through ``SynthesisService``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yomiage.application.synthesis import SynthesisFailure, SynthesisRequest, SynthesisService
from yomiage.core.models import VoiceIntent
from yomiage.ports.tts import ProviderErrorCategory

from .fakes import PROVIDER_KINDS, provider_factory


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PROVIDER_KINDS)
async def test_provider_health_and_voice_contract(kind: str, tmp_path: Path) -> None:
    provider, _fake = provider_factory(kind, tmp_path)
    service = SynthesisService(provider)

    health = await service.health()
    voices = await service.list_voices()

    assert health.healthy is True
    expected_provider = "voisona" if kind == "file" else kind
    assert health.provider == expected_provider
    assert voices and voices[0].id
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PROVIDER_KINDS)
async def test_provider_synthesis_contract(kind: str, tmp_path: Path) -> None:
    provider, fake = provider_factory(kind, tmp_path)
    service = SynthesisService(provider)

    result = await service.synthesize(
        SynthesisRequest(
            "contract text",
            voice_intent=VoiceIntent(preferred_profile=fake.name),
        )
    )

    assert result.is_success
    assert result.audio_data and result.format == "wav"
    assert result.provider == provider.name
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PROVIDER_KINDS)
async def test_provider_file_destination_contract(kind: str, tmp_path: Path) -> None:
    provider, _fake = provider_factory(kind, tmp_path)
    service = SynthesisService(provider)
    destination = tmp_path / f"{kind}.wav"

    result = await service.synthesize(
        SynthesisRequest("file contract", destination=str(destination))
    )

    assert result.is_success
    assert result.audio_data
    assert destination.read_bytes() == result.audio_data
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PROVIDER_KINDS)
async def test_provider_timeout_contract(kind: str, tmp_path: Path) -> None:
    provider, _fake = provider_factory(kind, tmp_path, delay=0.05)
    service = SynthesisService(provider, timeout=0.001)

    with pytest.raises(SynthesisFailure) as caught:
        await service.synthesize("timeout contract")

    assert caught.value.category is ProviderErrorCategory.TIMEOUT
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PROVIDER_KINDS)
async def test_provider_cancellation_contract(kind: str, tmp_path: Path) -> None:
    provider, fake = provider_factory(kind, tmp_path, delay=0.2)
    fallback, fallback_fake = provider_factory("port", tmp_path, name="fallback")
    service = SynthesisService(
        {provider.name: provider, fallback.name: fallback},
        primary=provider.name,
        fallback=fallback.name,
    )
    task = asyncio.create_task(service.synthesize("cancel contract"))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.cancelled == 1
    assert fallback_fake.calls == []
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PROVIDER_KINDS)
async def test_provider_close_contract_is_idempotent(kind: str, tmp_path: Path) -> None:
    provider, fake = provider_factory(kind, tmp_path)
    service = SynthesisService(provider)

    await service.close()
    await service.close()

    assert fake.close_calls == 1
