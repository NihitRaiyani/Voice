"""Transport-neutral AI provider contracts.

These interfaces are deliberately smaller than Pipecat/OpenAI/Sarvam objects.
Application code should depend on these request/result shapes and let concrete
adapters translate to SDK-specific payloads.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# if other role comes then this then it will be invalid role and will raise error
Role = Literal["system", "user", "assistant"]


class ProviderUnavailable(RuntimeError):
    """Raised when a selected provider has no usable local/cloud adapter yet."""


@dataclass(frozen=True)
class AudioFormat:
    sample_rate_hz: int
    channels: int = 1
    encoding: str = "pcm_s16le"


@dataclass(frozen=True)
class STTRequest:
    audio: bytes
    format: AudioFormat
    language_hint: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class STTResult:
    text: str
    language: str | None = None
    confidence: float | None = None


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(self, request: STTRequest) -> STTResult: ...


@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[LLMMessage, ...]
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMChunk:
    text: str
    finish_reason: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]: ...


@dataclass(frozen=True)
class TTSRequest:
    text: str
    language: str = "hi-IN"
    voice: str | None = None
    format: AudioFormat = field(default_factory=lambda: AudioFormat(sample_rate_hz=8000))
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    format: AudioFormat
    duration_ms: int | None = None


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize(self, request: TTSRequest) -> TTSResult: ...


@dataclass(frozen=True)
class EmbeddingRequest:
    texts: tuple[str, ...]
    model: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    dimension: int


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...


__all__ = [
    "AudioFormat",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResult",
    "LLMChunk",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "ProviderUnavailable",
    "STTProvider",
    "STTRequest",
    "STTResult",
    "TTSProvider",
    "TTSRequest",
    "TTSResult",
]
