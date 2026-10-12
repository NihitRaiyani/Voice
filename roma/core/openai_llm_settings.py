"""Environment-only OpenAI settings; no speech/DB keys needed by the text console."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenAILLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )
    llm_provider: Literal["mock", "openai"] = "mock"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = Field(default="", max_length=100)
    openai_max_output_tokens: int = Field(default=256, ge=1, le=512)
    openai_temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)
    openai_max_prompt_bytes: int = Field(default=16384, ge=256, le=65536)
    openai_attempt_timeout_secs: float = Field(default=4, gt=0, le=30, allow_inf_nan=False)
    openai_turn_timeout_secs: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    openai_max_retries: int = Field(default=1, ge=0, le=2)
    openai_retry_backoff_secs: float = Field(default=0.25, ge=0, le=2, allow_inf_nan=False)
    openai_call_token_budget: int = Field(default=20000, ge=1)
    openai_call_spend_cap_inr: float = Field(default=2, gt=0, allow_inf_nan=False)
