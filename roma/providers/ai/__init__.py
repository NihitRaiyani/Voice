"""Provider contracts and factories for AI model boundaries."""

from roma.providers.ai.contracts import (
    AudioFormat,
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResult,
    LLMChunk,
    LLMGenerationMetrics,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderUnavailable,
    STTProvider,
    STTRequest,
    STTResult,
    TTSProvider,
    TTSRequest,
    TTSResult,
)
from roma.providers.ai.mocks import (
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSTTProvider,
    MockTTSProvider,
)
from roma.providers.ai.registry import (
    build_embedding_provider,
    build_llm_provider,
    build_stt_provider,
    build_tts_provider,
)

__all__ = [
    "AudioFormat",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResult",
    "LLMChunk",
    "LLMGenerationMetrics",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "MockEmbeddingProvider",
    "MockLLMProvider",
    "MockSTTProvider",
    "MockTTSProvider",
    "ProviderUnavailable",
    "STTProvider",
    "STTRequest",
    "STTResult",
    "TTSProvider",
    "TTSRequest",
    "TTSResult",
    "build_embedding_provider",
    "build_llm_provider",
    "build_stt_provider",
    "build_tts_provider",
]
