"""Deterministic AI providers for tests and offline development."""

from __future__ import annotations

import hashlib
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass

from roma.providers.ai.contracts import (
    EmbeddingIdentity,
    EmbeddingRequest,
    EmbeddingResult,
    LLMChunk,
    LLMRequest,
    STTRequest,
    STTResult,
    TTSRequest,
    TTSResult,
)


@dataclass(frozen=True)
class MockSTTProvider:
    transcript: str = "mock transcript"
    language: str = "hi-IN"
    confidence: float = 1.0

    async def transcribe(self, request: STTRequest) -> STTResult:
        language = request.language_hint or self.language
        return STTResult(self.transcript, language=language, confidence=self.confidence)


@dataclass(frozen=True)
class MockLLMProvider:
    response: str = "mock response"
    chunk_size: int = 0

    async def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        text = self.response
        if self.chunk_size <= 0:
            yield LLMChunk(text=text, finish_reason="stop")
            return
        for offset in range(0, len(text), self.chunk_size):
            yield LLMChunk(text=text[offset : offset + self.chunk_size])
        yield LLMChunk(text="", finish_reason="stop")


@dataclass(frozen=True)
class MockTTSProvider:
    prefix: bytes = b"MOCK_TTS:"

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        audio = self.prefix + request.text.encode("utf-8")
        return TTSResult(audio=audio, format=request.format, duration_ms=0)


@dataclass(frozen=True)
class MockEmbeddingProvider:
    dimension: int = 8

    @property
    def identity(self) -> EmbeddingIdentity:
        return EmbeddingIdentity(
            "mock-sha256",
            "1",
            self.dimension,
            "sha256-v1",
            hashlib.sha256(b"mock-sha256-v1").hexdigest(),
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        vectors = tuple(self._vector(text) for text in request.texts)
        return EmbeddingResult(
            vectors=vectors, dimension=self.dimension, identity=self.identity
        )

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = []
        for index in range(self.dimension):
            byte = digest[index % len(digest)]
            values.append((byte / 127.5) - 1.0)
        norm = math.sqrt(sum(value * value for value in values))
        return tuple(value / norm for value in values)


__all__ = [
    "MockEmbeddingProvider",
    "MockLLMProvider",
    "MockSTTProvider",
    "MockTTSProvider",
]
