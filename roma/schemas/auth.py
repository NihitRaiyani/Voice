"""Bounded human-authentication request bodies; secrets have redacted representations."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class LoginRequest(AuthRequest):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=256)


class RefreshRequest(AuthRequest):
    refresh_token: SecretStr = Field(min_length=64, max_length=64)


class CreateUserRequest(AuthRequest):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=12, max_length=256)
    roles: set[str] = Field(min_length=1, max_length=3, strict=False)


class UpdateUserRequest(AuthRequest):
    roles: set[str] | None = Field(default=None, min_length=1, max_length=3, strict=False)
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.roles is None and self.is_active is None:
            raise ValueError("Supply a role or active-status change")
        return self
