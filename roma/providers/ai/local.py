"""Named local/open-source provider placeholders for later levels.

Level 1 owns dependency inversion and mockability. Actual model loading,
hardware admission and quality benchmarks land in the model-specific levels.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import NoReturn

from roma.providers.ai.contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    LLMChunk,
    LLMRequest,
    ProviderUnavailable,
    STTRequest,
    STTResult,
    TTSRequest,
    TTSResult,
)


@dataclass(frozen=True)
class _UnavailableProvider:
    provider_name: str
    level: str

    def _raise(self) -> NoReturn:
        raise ProviderUnavailable(
            f"{self.provider_name} is selected but not implemented in this level; "
            f"complete {self.level} and its model/config checks first"
        )


class IndicConformerSTT(_UnavailableProvider):
    async def transcribe(self, request: STTRequest) -> STTResult:
        self._raise()


class WhisperSTT(_UnavailableProvider):
    async def transcribe(self, request: STTRequest) -> STTResult:
        self._raise()


class Qwen3TransformersLLM(_UnavailableProvider):
    async def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        self._raise()
        yield LLMChunk("")


class Qwen3VLLMClient(_UnavailableProvider):
    async def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        self._raise()
        yield LLMChunk("")


class IndicTTSProvider(_UnavailableProvider):
    async def synthesize(self, request: TTSRequest) -> TTSResult:
        self._raise()


class LocalMultilingualEmbedding(_UnavailableProvider):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self._raise()


__all__ = [
    "IndicConformerSTT",
    "IndicTTSProvider",
    "LocalMultilingualEmbedding",
    "Qwen3TransformersLLM",
    "Qwen3VLLMClient",
    "WhisperSTT",
]
