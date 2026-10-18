"""Local speech compatibility exports and later-level TTS/embedding placeholders.

Level 1 owns dependency inversion and mockability. Actual model loading,
hardware admission and quality benchmarks land in the model-specific levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from roma.providers.ai.contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    ProviderUnavailable,
    TTSRequest,
    TTSResult,
)
from roma.providers.ai.stt import IndicConformerSTT, WhisperSTT


@dataclass(frozen=True)
class _UnavailableProvider:
    provider_name: str
    level: str

    def _raise(self) -> NoReturn:
        raise ProviderUnavailable(
            f"{self.provider_name} is selected but not implemented in this level; "
            f"complete {self.level} and its model/config checks first"
        )


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
    "WhisperSTT",
]
