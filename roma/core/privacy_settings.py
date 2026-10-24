"""Explicit privacy setup; no keys, consent or retention policy are invented."""

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PrivacySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )
    pii_encryption_enabled: bool = False
    pii_encryption_key: SecretStr = SecretStr("")
    recording_url_signing_key: SecretStr = SecretStr("")
    recording_url_ttl_secs: int = Field(default=120, ge=1, le=300)
    privacy_retention_days: int | None = Field(default=None, ge=1, le=3650)
    roma_data_dir: str = "var/roma"

    @model_validator(mode="after")
    def distinct_keys(self):
        if self.pii_encryption_enabled and self.recording_url_signing_key.get_secret_value():
            try:
                encryption_key = bytes.fromhex(self.pii_encryption_key.get_secret_value())
                signing_key = bytes.fromhex(self.recording_url_signing_key.get_secret_value())
                if len(encryption_key) != 32 or len(signing_key) != 32:
                    raise ValueError
            except ValueError:
                raise ValueError(
                    "Distinct 32-byte hexadecimal privacy keys are required."
                ) from None
            if encryption_key == signing_key:
                raise ValueError(
                    "Privacy encryption and recording signing keys must be distinct."
                )
        return self
