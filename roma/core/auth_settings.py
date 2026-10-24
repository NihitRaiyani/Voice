"""Separate, fail-closed human authentication settings; no default signing secret."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    auth_allow_legacy_api_token: bool = False
    auth_rate_limit: int = Field(default=10, ge=1, le=1000)
    auth_rate_window_secs: int = Field(default=300, ge=1, le=3600)
    auth_jwt_secret: SecretStr | None = None
    auth_jwt_issuer: str = Field(default="roma", min_length=1, max_length=128)
    auth_jwt_audience: str = Field(default="roma-api", min_length=1, max_length=128)
    auth_access_ttl_secs: int = Field(default=900, ge=60, le=3600)
    auth_refresh_ttl_secs: int = Field(default=604800, ge=3600, le=2592000)

    @field_validator("auth_jwt_secret")
    @classmethod
    def strong_secret(cls, value):
        if value is not None and value.get_secret_value() == "":
            return None  # Blank template value disables auth; never invent a secret.
        if value is not None and len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("AUTH_JWT_SECRET must contain at least 32 bytes")
        return value
