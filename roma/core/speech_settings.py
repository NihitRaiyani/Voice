"""Opt-in speech-lab configuration; reading settings never loads or downloads models."""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SpeechSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    stt_provider: Literal["mock", "indic_conformer", "whisper", "hf_indic_conformer"] = "mock"
    stt_language: Literal["hi", "gu", "en"] = "hi"
    stt_timeout_secs: float = Field(default=30, gt=0, le=60, allow_inf_nan=False)
    stt_max_audio_secs: float = Field(default=30, ge=1, le=30, allow_inf_nan=False)
    indic_model_id: str = Field(
        default="ai4bharat/indic-conformer-600m-multilingual", min_length=1, max_length=256
    )
    indic_model_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    indic_trust_remote_code: bool = False
    indic_decoder: Literal["ctc", "rnnt"] = "ctc"
    whisper_model_id: str = Field(default="small", min_length=1, max_length=256)
    hf_asr_endpoint_url: str = Field(default="", max_length=2048)
    hf_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
            "HUGGINGFACE_API_KEY",
            "HUGGING_FACE_API_KEY",
            "hf_token",
        ),
    )
