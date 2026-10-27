"""Explicit carrier speech profile; runtime activation is separate from acceptance."""
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class TelephonySettings(BaseSettings):
    voice_speech_mode: Literal['legacy_cloud', 'local_http'] = 'legacy_cloud'
    voice_media_send_timeout_secs: float = Field(default=1, gt=0, le=10, allow_inf_nan=False)
    voice_rag_timeout_secs: float = Field(default=10, gt=0, le=30, allow_inf_nan=False)
    voice_rag_filler_after_secs: float = Field(default=.15, gt=0, le=2, allow_inf_nan=False)
