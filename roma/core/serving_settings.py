"""Separate, opt-in local speech HTTP identity and capacity configuration."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SpeechServingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )
    speech_service_url: str = "http://127.0.0.1:8091"
    speech_service_key: SecretStr = SecretStr("")
    speech_service_timeout_secs: float = Field(default=30, gt=0, le=180, allow_inf_nan=False)
    speech_service_max_inflight: int = Field(default=1, ge=1, le=2)
    speech_service_max_body_bytes: int = Field(default=3000000, ge=1024, le=6000000)
    speech_service_max_audio_secs: float = Field(default=30, gt=0, le=30, allow_inf_nan=False)
    speech_service_max_text_bytes: int = Field(default=4096, ge=128, le=8192)
    speech_service_startup_secs: float = Field(default=180, gt=0, le=300, allow_inf_nan=False)
    speech_service_shutdown_secs: float = Field(default=35, gt=0, le=180, allow_inf_nan=False)
    # Operator-supplied account limits, unknown until verified for the organization.
    # These are inventory fields, not claims of a distributed Claude rate limiter.
    anthropic_org_rpm: int | None = Field(default=None, ge=1)
    anthropic_org_itpm: int | None = Field(default=None, ge=1)
    anthropic_org_otpm: int | None = Field(default=None, ge=1)

    @field_validator("speech_service_url")
    @classmethod
    def loopback_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError:
            raise ValueError("Speech service requires a literal loopback HTTP URL") from None
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "::1"}
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or port is None
            or not 1 <= port <= 65535
        ):
            raise ValueError("Speech service requires a literal loopback HTTP URL with port")
        return value.rstrip("/")

    def require_key(self) -> str:
        key = self.speech_service_key.get_secret_value()
        if (
            not 32 <= len(key) <= 512
            or not key.isascii()
            or any(not 33 <= ord(c) <= 126 for c in key)
        ):
            raise ValueError(
                "Set a separate private SPEECH_SERVICE_KEY of 32–512 ASCII characters"
            )
        return key
