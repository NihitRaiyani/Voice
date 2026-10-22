"""Fixed, PII-safe error categories for the isolated voice prototype."""

import asyncio
from contextlib import contextmanager
from enum import StrEnum

from sqlalchemy.exc import SQLAlchemyError

from roma.core.tokenizer import TOKENIZER_SETUP_MESSAGE, TokenizerSetupError
from roma.domain.appointments.booking import BookingError


class VoiceErrorCode(StrEnum):
    ASR = "ASR"
    TTS = "TTS"
    DB = "DB"
    DEVICE = "DEVICE"
    CAPTURE = "CAPTURE"
    LLM = "LLM"
    SAFETY = "SAFETY"
    BOOKING_DATA = "BOOKING-DATA"
    CANCELLED = "CANCELLED"
    TOKENIZER_DATA_MISSING = "TOKENIZER-DATA-MISSING"

    @property
    def message(self) -> str:
        if self == self.TOKENIZER_DATA_MISSING:
            return TOKENIZER_SETUP_MESSAGE
        return f"{self.value}: voice session stopped."


class VoiceFailure(RuntimeError):
    def __init__(self, code: VoiceErrorCode):
        self.code = code
        super().__init__(code.message)


_LEGACY = {
    "stt-unavailable": VoiceErrorCode.ASR,
    "stt-unsupported-or-invalid-request": VoiceErrorCode.ASR,
    "stt-turn-too-long": VoiceErrorCode.ASR,
    "asr-unavailable": VoiceErrorCode.ASR,
    "asr-empty": VoiceErrorCode.ASR,
    "listen-timeout": VoiceErrorCode.CAPTURE,
    "extraction-unavailable-or-invalid": VoiceErrorCode.LLM,
    "generation-unavailable-or-invalid": VoiceErrorCode.LLM,
    "safety-audit-unavailable": VoiceErrorCode.SAFETY,
    "BOOKING_UNAVAILABLE": VoiceErrorCode.BOOKING_DATA,
}


def map_voice_error(error: object, *, stage: VoiceErrorCode = VoiceErrorCode.CAPTURE) -> VoiceErrorCode:
    """Map trusted codes/types/context only; never inspect or return exception text."""
    if isinstance(error, VoiceFailure):
        return error.code
    if isinstance(error, VoiceErrorCode):
        return error
    if isinstance(error, TokenizerSetupError):
        return VoiceErrorCode.TOKENIZER_DATA_MISSING
    if isinstance(error, asyncio.CancelledError):
        return VoiceErrorCode.CANCELLED
    if isinstance(error, SQLAlchemyError):
        return VoiceErrorCode.DB
    if isinstance(error, BookingError):
        return VoiceErrorCode.BOOKING_DATA
    if stage == VoiceErrorCode.CAPTURE and (
        isinstance(error, OSError)
        or (type(error).__module__ == "sounddevice" and type(error).__name__ == "PortAudioError")
    ):
        return VoiceErrorCode.DEVICE
    if isinstance(error, str):
        # Controller errors can contain multiple fixed legacy codes.
        codes = [_LEGACY.get(part) for part in error.split(",")]
        if codes and all(code is not None for code in codes):
            first = codes[0]
            assert first is not None
            return VoiceErrorCode.SAFETY if VoiceErrorCode.SAFETY in codes else first
        try:
            return VoiceErrorCode(error)
        except ValueError:
            pass
    return stage


@contextmanager
def voice_boundary(stage: VoiceErrorCode):
    try:
        yield
    except Exception as exc:  # noqa: BLE001 -- sanitize provider/device/database failures.
        raise VoiceFailure(map_voice_error(exc, stage=stage)) from None
