"""Compatibility exports for explicitly provisioned cache-only local providers."""

from __future__ import annotations

from roma.providers.ai.embeddings import LocalMultilingualEmbedding
from roma.providers.ai.indic_tts import IndicTTSProvider
from roma.providers.ai.stt import IndicConformerSTT, WhisperSTT

__all__ = [
    "IndicConformerSTT",
    "IndicTTSProvider",
    "LocalMultilingualEmbedding",
    "WhisperSTT",
]
