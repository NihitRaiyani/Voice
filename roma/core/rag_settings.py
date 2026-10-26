"""Opt-in governed retrieval; owner-approved lab thresholds remain separate gates."""
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from roma.core.embedding_settings import EmbeddingSettings


class RAGSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
        env_ignore_empty=True, hide_input_in_errors=True,
    )
    rag_enabled: bool = False
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_min_score: float = Field(default=.8, ge=-1, le=1, allow_inf_nan=False)
    rag_retrieval_timeout_secs: float = Field(default=.45, gt=0, le=10, allow_inf_nan=False)
    rag_generation_timeout_secs: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)


class KnowledgeRuntimeSettings(EmbeddingSettings, RAGSettings):
    embedding_provider: Literal['mock', 'local_multilingual'] = 'local_multilingual'
