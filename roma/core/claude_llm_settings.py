"""Environment-only Claude configuration; settings never instantiate a client."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClaudeLLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )
    llm_provider: Literal["mock", "claude"] = "claude"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = Field(default="claude-haiku-5-5", min_length=1, max_length=100)
    anthropic_effort: Literal["low"] = "low"
    anthropic_prompt_cache: bool = True
    anthropic_max_output_tokens: int = Field(default=256, ge=1, le=512)
    anthropic_max_prompt_bytes: int = Field(default=16384, ge=256, le=65536)
    anthropic_attempt_timeout_secs: float = Field(default=4, gt=0, le=30, allow_inf_nan=False)
    anthropic_turn_timeout_secs: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    anthropic_max_retries: int = Field(default=1, ge=0, le=2)
    anthropic_retry_backoff_secs: float = Field(default=0.25, ge=0, le=2, allow_inf_nan=False)
    anthropic_call_token_budget: int = Field(default=20000, ge=1)
    anthropic_call_spend_cap_inr: float = Field(default=2, gt=0, allow_inf_nan=False)
