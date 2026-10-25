"""Opt-in speech-lab configuration; reading settings never loads or downloads models."""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from roma.domain.audio.turn_policy import TurnPolicy


class SpeechSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    stt_provider: Literal[
        "mock",
        "indic_conformer",
        "whisper",
        "whisper_mlx",
        "hf_indic_conformer",
        "hf_public_lab",
        "hf_whisper",
    ] = "mock"
    voice_endpoint_silence_ms: int = Field(default=TurnPolicy().endpoint_silence_ms, gt=0, le=2000)
    voice_continuation_grace_ms: int = Field(default=TurnPolicy().continuation_grace_ms, ge=0, le=2000)
    voice_batch_gap_min_ms: int = Field(default=TurnPolicy().batch_gap_min_ms, gt=0, le=2000)
    voice_max_utterance_ms: int = Field(default=TurnPolicy().max_utterance_ms, gt=0, le=30000)
    voice_batch_min_turn_ms: int = Field(default=TurnPolicy().batch_min_turn_ms, ge=0, le=30000)

    def turn_policy(self) -> TurnPolicy:
        return TurnPolicy(
            self.voice_endpoint_silence_ms, self.voice_continuation_grace_ms,
            self.voice_batch_gap_min_ms, min(self.voice_max_utterance_ms, int(self.stt_max_audio_secs * 1000)),
            self.voice_batch_min_turn_ms,
        )

    whisper_batch_no_speech: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
    whisper_booking_vocabulary: bool = False
    whisper_batch_avg_logprob: float = Field(default=-1.0, le=0, allow_inf_nan=False)
    whisper_batch_compression_ratio: float = Field(default=2.4, gt=0, allow_inf_nan=False)

    def batch_rejection(self):
        from roma.providers.ai.mlx_batch import BatchRejectionPolicy
        return BatchRejectionPolicy(self.whisper_batch_no_speech, self.whisper_batch_avg_logprob,
                                    self.whisper_batch_compression_ratio)

    voice_turn_budget_ms: dict[str, float] | None = None

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
    whisper_mlx_model_dir: str = Field(default="var/roma/models/whisper-large-v3-turbo-mlx-4bit", min_length=1, max_length=1024)
    hf_public_timeout_secs: float = Field(default=90, gt=0, le=120, allow_inf_nan=False)
    hf_asr_endpoint_url: str = Field(default="", max_length=2048)
    hf_indic_space_url: str = Field(
        default="https://1219naveen-indicconformer-asr.hf.space", max_length=2048
    )
    hf_whisper_space_url: str = Field(
        default="https://hf-audio-whisper-large-v3.hf.space", max_length=2048
    )
    hf_whisper_space_authenticated: bool = False
    hf_whisper_api_name: Literal["predict", "transcribe"] = "transcribe"
    hf_whisper_model: Literal["openai/whisper-large-v3", "openai/whisper-large-v3-turbo"] = (
        "openai/whisper-large-v3-turbo"
    )
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
