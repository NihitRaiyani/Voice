"""Opt-in local TTS lab settings; no imports, downloads or model loads on construction."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TTSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)
    tts_provider: Literal["mock", "indic_tts"] = "mock"
    indic_tts_manifest: str = Field(default="", max_length=2048)
    indic_tts_python: str = Field(default="", max_length=2048)
    tts_voice: Literal["female", "male"] = "female"
    tts_timeout_secs: float = Field(default=60, gt=0, le=180, allow_inf_nan=False)
    tts_max_text_bytes: int = Field(default=4096, ge=128, le=8192)
    tts_max_audio_secs: float = Field(default=30, gt=0, le=60, allow_inf_nan=False)
    tts_max_concurrency: int = Field(default=1, ge=1, le=4)
    tts_cache_entries: int = Field(default=64, ge=0, le=256)
    tts_cache_bytes: int = Field(default=8388608, ge=0, le=33554432)
