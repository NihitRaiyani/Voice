from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from roma.core.config import Settings
from roma.providers.ai import (
    AudioFormat,
    EmbeddingRequest,
    EmbeddingResult,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSTTProvider,
    MockTTSProvider,
    ProviderUnavailable,
    STTProvider,
    STTRequest,
    TTSProvider,
    TTSRequest,
    build_embedding_provider,
    build_llm_provider,
    build_stt_provider,
    build_tts_provider,
)


def _settings(**overrides) -> Settings:
    values = {
        "sarvam_api_key": "sarvam-test",
        "openai_api_key": "openai-test",
        "redis_url": "redis://localhost:6379/0",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_settings_default_ai_providers_are_mock_and_test_safe():
    settings = _settings()

    assert settings.stt_provider == "mock"
    assert settings.llm_provider == "mock"
    assert settings.tts_provider == "mock"
    assert settings.embedding_provider == "mock"

    assert isinstance(build_stt_provider(settings), MockSTTProvider)
    assert isinstance(build_llm_provider(settings), MockLLMProvider)
    assert isinstance(build_tts_provider(settings), MockTTSProvider)
    assert isinstance(build_embedding_provider(settings), MockEmbeddingProvider)


def test_invalid_provider_names_fail_at_settings_boundary():
    with pytest.raises(ValidationError):
        _settings(llm_provider="openai-live")


def test_mock_stt_satisfies_protocol_and_returns_language_hint():
    async def run() -> None:
        provider = MockSTTProvider(transcript="હા", language="gu-IN")
        assert isinstance(provider, STTProvider)
        result = await provider.transcribe(
            STTRequest(
                audio=b"\0\0",
                format=AudioFormat(sample_rate_hz=16000),
                language_hint="hi-IN",
            )
        )
        assert result.text == "હા"
        assert result.language == "hi-IN"
        assert result.confidence == 1.0

    asyncio.run(run())


def test_mock_llm_streams_chunks_through_the_provider_interface():
    async def run() -> None:
        provider = MockLLMProvider(response="namaste", chunk_size=3)
        assert isinstance(provider, LLMProvider)
        chunks = [
            chunk
            async for chunk in provider.generate(
                LLMRequest(messages=(LLMMessage(role="user", content="hello"),))
            )
        ]
        assert [chunk.text for chunk in chunks] == ["nam", "ast", "e", ""]
        assert chunks[-1].finish_reason == "stop"

    asyncio.run(run())


def test_mock_tts_returns_deterministic_audio_without_provider_keys():
    async def run() -> None:
        provider = MockTTSProvider()
        assert isinstance(provider, TTSProvider)
        result = await provider.synthesize(TTSRequest(text="Roma bol rahi hoon"))
        assert result.audio.startswith(b"MOCK_TTS:")
        assert result.format.sample_rate_hz == 8000

    asyncio.run(run())


def test_mock_embedding_is_deterministic_and_shapes_vectors():
    async def run() -> None:
        provider = MockEmbeddingProvider(dimension=4)
        first = await provider.embed(EmbeddingRequest(texts=("fees", "placement")))
        second = await provider.embed(EmbeddingRequest(texts=("fees", "placement")))
        assert isinstance(first, EmbeddingResult)
        assert first == second
        assert first.dimension == 4
        assert len(first.vectors) == 2
        assert all(len(vector) == 4 for vector in first.vectors)

    asyncio.run(run())


def test_named_future_local_provider_fails_fast_until_its_level_is_implemented():
    async def run() -> None:
        provider = build_llm_provider(_settings(llm_provider="qwen3_transformers"))
        with pytest.raises(ProviderUnavailable, match="Level 2"):
            async for _chunk in provider.generate(
                LLMRequest(messages=(LLMMessage(role="user", content="hello"),))
            ):
                pass

    asyncio.run(run())
