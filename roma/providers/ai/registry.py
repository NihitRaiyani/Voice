"""Config-driven AI provider selection."""

from __future__ import annotations

from roma.providers.ai.contracts import (
    EmbeddingProvider,
    LLMProvider,
    STTProvider,
    TTSProvider,
)
from roma.providers.ai.local import (
    IndicConformerSTT,
    IndicTTSProvider,
    LocalMultilingualEmbedding,
    Qwen3TransformersLLM,
    Qwen3VLLMClient,
    WhisperSTT,
)
from roma.providers.ai.mocks import (
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSTTProvider,
    MockTTSProvider,
)


def build_stt_provider(settings) -> STTProvider:
    match settings.stt_provider:
        case "mock":
            return MockSTTProvider()
        case "indic_conformer":
            return IndicConformerSTT("IndicConformerSTT", "Level 4")
        case "whisper":
            return WhisperSTT("WhisperSTT", "Level 4")
    raise ValueError(f"unknown STT provider {settings.stt_provider!r}")


def build_llm_provider(settings) -> LLMProvider:
    match settings.llm_provider:
        case "mock":
            return MockLLMProvider()
        case "qwen3_transformers":
            return Qwen3TransformersLLM(settings)
        case "qwen3_vllm":
            return Qwen3VLLMClient("Qwen3VLLMClient", "Level 10")
    raise ValueError(f"unknown LLM provider {settings.llm_provider!r}")


def build_tts_provider(settings) -> TTSProvider:
    match settings.tts_provider:
        case "mock":
            return MockTTSProvider()
        case "indic_tts":
            return IndicTTSProvider("IndicTTSProvider", "Level 5")
    raise ValueError(f"unknown TTS provider {settings.tts_provider!r}")


def build_embedding_provider(settings) -> EmbeddingProvider:
    match settings.embedding_provider:
        case "mock":
            return MockEmbeddingProvider()
        case "local_multilingual":
            return LocalMultilingualEmbedding("LocalMultilingualEmbedding", "Level 9")
    raise ValueError(f"unknown embedding provider {settings.embedding_provider!r}")


__all__ = [
    "build_embedding_provider",
    "build_llm_provider",
    "build_stt_provider",
    "build_tts_provider",
]
