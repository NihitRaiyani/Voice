"""Outbound TTS audio sanitizer (root-cause fix for the garbled-audio bug).

Sits between Bulbul TTS and `transport.output()`. Fixes a corruption that only shows
up on the live *streaming* path, never on the single canned frame:

Sarvam's linear16 streaming websocket carries a **WAV/RIFF header** on the opening chunk
of a synthesis (its own docs write the concatenated chunks straight to `output.wav`, which
only yields a valid file because the stream is WAV-framed). Pipecat's websocket receive
path (`SarvamTTSService._receive_messages`) base64-decodes each chunk and wraps it directly
as a `TTSAudioRawFrame` with **no header strip and no sample alignment** — unlike its own
HTTP path (`run_tts`), which does `if audio.startswith(b"RIFF"): audio = audio[44:]`. So the
44 header bytes land in the output PCM16 buffer, byte-shift every following sample by one
byte, and the rest of the stream plays as robotic noise. The pre-buffered canned frame never
hits this path, which is why it sounds clean.

This processor mirrors the HTTP path's guards on the streamed frames:
1. strip a leading 44-byte WAV header from any chunk that starts with ``b"RIFF"``,
2. keep PCM16 (even-byte) alignment across chunks, carrying a trailing odd byte forward.

Carry state is reset at every synthesis/context boundary (`TTSStartedFrame`, a new
`context_id`, a fresh RIFF header), on teardown (`CancelFrame`/`EndFrame`), and on
`InterruptionFrame` so a held half-sample can never bleed across turns or survive a
barge-in.

Sarvam's vendored `tts.py` lives under `.venv/` and must not be edited; fixing here also keeps
us off pipecat's private `_receive_messages`, which the turn-taking/services API changes often.
"""

import logging

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_log = logging.getLogger("roma.realtime")

_WAV_HEADER_BYTES = 44


class TTSAudioSanitizer(FrameProcessor):
    """Strip stray WAV headers and hold PCM16 alignment on the streamed TTS path."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._carry = b""
        self._ctx = None
        self._logged_strip = False

    def _sanitize(self, data: bytes) -> bytes:
        if len(data) >= 4 and data[:4] == b"RIFF":
            self._carry = b""
            if not self._logged_strip:
                self._logged_strip = True
                _log.info(
                    "TTS sanitizer: Sarvam WS chunk carried a WAV header; stripping "
                    "(root cause of the garbled audio)"
                )
            data = data[_WAV_HEADER_BYTES:] if len(data) > _WAV_HEADER_BYTES else b""

        data = self._carry + data
        self._carry = b""
        if len(data) & 1:
            self._carry = data[-1:]
            data = data[:-1]
        return data

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, (TTSStartedFrame, CancelFrame, EndFrame, InterruptionFrame)):
            self._carry = b""
            self._ctx = None

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TTSAudioRawFrame):
            if frame.context_id != self._ctx:
                self._carry = b""
                self._ctx = frame.context_id
            cleaned = self._sanitize(frame.audio)
            if not cleaned:
                return
            out = TTSAudioRawFrame(
                cleaned, frame.sample_rate, frame.num_channels, context_id=frame.context_id
            )
            await self.push_frame(out, direction)
            return

        await self.push_frame(frame, direction)


__all__ = ["TTSAudioSanitizer"]
