"""Explicit cache-only embedding selection; defaults never provision artifacts."""

from pydantic import Field
from pydantic_settings import BaseSettings


class EmbeddingSettings(BaseSettings):
    embedding_manifest: str = ""
    embedding_timeout_secs: float = Field(default=10.0, gt=0, le=60, allow_inf_nan=False)
    embedding_max_text_bytes: int = Field(default=8192, ge=1, le=8192)
    embedding_max_batch_size: int = Field(default=4, ge=1, le=4)
    embedding_cpu_threads: int = Field(default=2, ge=1, le=2)
