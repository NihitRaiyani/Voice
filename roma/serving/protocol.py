"""Strict bounded wire shapes shared by the local service and its clients."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from roma.core.ai_contracts import AudioFormat, STTRequest, STTResult, TTSRequest, TTSResult
from roma.core.serving_settings import SpeechServingSettings

MetadataKey = Annotated[str, StringConstraints(min_length=1, max_length=64)]
MetadataValue = Annotated[str, StringConstraints(max_length=256)]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Format(WireModel):
    sample_rate_hz: Literal[8000, 16000]
    channels: Literal[1] = 1
    encoding: Literal["pcm_s16le"] = "pcm_s16le"

    @field_validator("sample_rate_hz", "channels", mode="before")
    @classmethod
    def integer_only(cls, value):
        if type(value) is not int:
            raise ValueError("Audio format requires integers")
        return value

    def contract(self) -> AudioFormat:
        return AudioFormat(**self.model_dump())


class STTBody(WireModel):
    audio_b64: str = Field(min_length=4, max_length=1280000)
    format: Format
    language_hint: Literal["hi", "gu", "en", "hi-IN", "gu-IN", "en-IN"] | None = None
    metadata: dict[MetadataKey, MetadataValue] = Field(default_factory=dict, max_length=16)
    turn_epoch: int | None = Field(default=None, ge=0)

    def contract(self, settings: SpeechServingSettings) -> STTRequest:
        audio = decode_audio(
            self.audio_b64, int(settings.speech_service_max_audio_secs * 32000)
        )
        if self.format.sample_rate_hz != 16000:
            raise ValueError("STT requires 16 kHz mono PCM16")
        if any(len(k.encode()) > 64 or len(v.encode()) > 256 for k, v in self.metadata.items()):
            raise ValueError("Metadata byte bound exceeded")
        return STTRequest(
            audio, self.format.contract(), self.language_hint, self.metadata, self.turn_epoch
        )


class STTBatchBody(WireModel):
    requests: list[STTBody] = Field(min_length=1, max_length=2)


class TTSBody(WireModel):
    text: str = Field(min_length=1, max_length=8192)
    language: Literal["hi", "hi-IN", "gu", "gu-IN", "en", "en-IN", "en-US", "en-GB"] = "hi-IN"
    voice: Literal["female", "male"] | None = None
    format: Format
    metadata: dict[MetadataKey, MetadataValue] = Field(default_factory=dict, max_length=16)
    turn_epoch: int | None = Field(default=None, ge=0)

    def contract(self, settings: SpeechServingSettings) -> TTSRequest:
        from roma.providers.ai.indic_tts import validate_request

        request = TTSRequest(
            self.text,
            self.language,
            self.voice,
            self.format.contract(),
            self.metadata,
            self.turn_epoch,
        )
        validate_request(request, settings.speech_service_max_text_bytes)
        return request


class STTResponse(WireModel):
    text: str = Field(max_length=32768)
    language: Literal["hi", "gu", "en"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    error_code: Literal["ASR-BATCH-REJECTED"] | None = None
    turn_epoch: int | None = Field(default=None, ge=0)

    def contract(self) -> STTResult:
        return STTResult(**self.model_dump())


class TTSResponse(WireModel):
    audio_b64: str = Field(min_length=4, max_length=2560000)
    format: Format
    duration_ms: int | None = Field(default=None, ge=0, le=60000)
    turn_epoch: int | None = Field(default=None, ge=0)

    def contract(self, max_secs: float) -> TTSResult:
        audio = decode_audio(self.audio_b64, int(max_secs * self.format.sample_rate_hz * 2))
        return TTSResult(audio, self.format.contract(), self.duration_ms, self.turn_epoch)


def decode_audio(value: str, maximum: int) -> bytes:
    try:
        audio = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid PCM base64") from None
    if not audio or len(audio) % 2 or len(audio) > maximum:
        raise ValueError("PCM must be nonempty, sample aligned and bounded")
    return audio


def stt_payload(request: STTRequest) -> dict:
    body = asdict(request)
    body["audio_b64"] = base64.b64encode(body.pop("audio")).decode("ascii")
    return body


def tts_payload(request: TTSRequest) -> dict:
    return asdict(request)


def stt_response(result: STTResult, epoch: int | None) -> dict:
    if result.turn_epoch not in (None, epoch):
        raise ValueError("Stale speech response")
    return STTResponse.model_validate({**asdict(result), "turn_epoch": epoch}).model_dump()


def tts_response(result: TTSResult, epoch: int | None, max_secs: float) -> dict:
    if result.turn_epoch not in (None, epoch):
        raise ValueError("Stale speech response")
    body = asdict(result)
    body["audio_b64"] = base64.b64encode(body.pop("audio")).decode("ascii")
    response = TTSResponse.model_validate({**body, "turn_epoch": epoch})
    response.contract(max_secs)
    return response.model_dump()


class TTSIdentityResponse(WireModel):
    # Opaque content identity only: never filesystem paths or model text.
    identity: str = Field(pattern=r"^indic-tts:[A-Za-z0-9_-]{1,128}(?::[A-Za-z0-9_-]{1,100})?$")
