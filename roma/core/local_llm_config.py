"""Local text inference settings; no telephony/cloud/database keys required."""

import json
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalLLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )
    llm_provider: Literal["mock", "qwen3_transformers", "qwen3_mlx", "qwen3_vllm"] = "mock"
    local_llm_model: str = "Qwen/Qwen3-8B"
    local_llm_revision: str = "main"
    local_llm_device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    local_llm_dtype: Literal["auto", "bf16", "fp16", "fp32", "int4"] = "auto"
    local_llm_local_files_only: bool = True
    local_llm_max_new_tokens: int = Field(default=128, ge=1, le=512)
    local_llm_max_prompt_tokens: int = Field(default=8192, ge=64, le=32768)
    local_llm_prefix_cache: bool = True
    local_llm_temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)
    local_llm_generation_timeout_secs: float = Field(default=120, gt=0, allow_inf_nan=False)
    hf_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
            "HUGGINGFACE_API_KEY",
            "HUGGING_FACE_API_KEY",
            "HF_API_KEY",
            "hf_token",
        ),
    )


def load_local_llm_settings(
    profile: str | Path | None = None, *, provider: str | None = None
) -> LocalLLMSettings:
    """Optional non-secret profile overrides dotenv; credentials stay in the environment."""
    values = json.loads(Path(profile).read_text(encoding="utf-8")) if profile else {}
    allowed = {
        name for name in LocalLLMSettings.model_fields if name.startswith("local_llm_")
    } | {"llm_provider"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError(
            "Local LLM profiles may contain only non-secret model/provider settings"
        )
    if provider is not None:
        values["llm_provider"] = provider
    return LocalLLMSettings(**values)
