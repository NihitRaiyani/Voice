"""Twilio protocol validation before native decoding; no new playout delay in TCP.

The offline audio domain owns the student codec/resampler/jitter implementation.
This adapter retains Pipecat control/clear/hangup and sample-rate behavior, while
counting per-stream sequence anomalies and rejecting malformed audio safely.
"""

import base64
import binascii
import json
from dataclasses import asdict, dataclass

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.serializers.twilio import TwilioFrameSerializer

MAX_MEDIA_BYTES = 8000  # one second, variable-size carrier messages allowed


@dataclass
class StreamAudioMetrics:
    accepted_media: int = 0
    sequence_gaps: int = 0  # inbound media.chunk gaps, not global control-message gaps
    duplicates_or_reordered: int = 0
    invalid_messages: int = 0
    pcm_alignment_errors: int = 0
    decoded_pcm_bytes: int = 0

    def snapshot(self):
        return asdict(self)


def _positive(value) -> int:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("Expected a positive sequence counter")
    if isinstance(value, str) and (
        not value.isascii() or not value.isdigit() or len(value) > 18
    ):
        raise ValueError("Expected a bounded decimal counter")
    number = int(value)
    if not 1 <= number < 2**63:
        raise ValueError("Expected a positive sequence counter")
    return number


class ValidatedTwilioSerializer(TwilioFrameSerializer):
    def __init__(self, stream_sid: str, **kwargs) -> None:
        super().__init__(stream_sid=stream_sid, **kwargs)
        self._expected_stream = stream_sid
        self.audio_metrics = StreamAudioMetrics()
        self._last_message = 0
        self._last_chunk = 0
        self._last_timestamp = -1

    async def deserialize(self, data: str | bytes):
        try:
            # Bound parsing work; base64 expands an 8 kB payload to about 11 kB.
            if len(data) > 16384:
                raise ValueError("Oversized media envelope")
            message = json.loads(data)
            if not isinstance(message, dict):
                raise ValueError("Expected media object")
            if message.get("event") != "media":
                # Controls share the global sequence but never increment media-gap counts.
                raw_sequence = message.get("sequenceNumber")
                control_sequence = _positive(raw_sequence) if raw_sequence is not None else None
                if control_sequence is not None and control_sequence <= self._last_message:
                    self.audio_metrics.duplicates_or_reordered += 1
                    return None
                result = await super().deserialize(data)
                if control_sequence is not None:
                    self._last_message = control_sequence
                return result
            if message.get("streamSid") != self._expected_stream:
                raise ValueError("Unexpected stream identity")
            media = message["media"]
            if not isinstance(media, dict) or media.get("track") != "inbound":
                raise ValueError("Expected inbound audio")
            sequence, chunk = (
                _positive(message.get("sequenceNumber")),
                _positive(media.get("chunk")),
            )
            # Monotonicity is checked BEFORE decoding. Global sequence includes DTMF/mark;
            # only media.chunk differences count as missing inbound media messages.
            if sequence <= self._last_message or chunk <= self._last_chunk:
                self.audio_metrics.duplicates_or_reordered += 1
                return None
            timestamp = media.get("timestamp")
            if (
                not isinstance(timestamp, str)
                or not timestamp.isascii()
                or not timestamp.isdigit()
                or len(timestamp) > 18
            ):
                raise ValueError("Expected a nonnegative media timestamp")
            if int(timestamp) < self._last_timestamp:
                raise ValueError("Media timestamp regressed")
            payload = media["payload"]
            if not isinstance(payload, str):
                raise ValueError("Expected base64 audio")
            decoded = base64.b64decode(payload, validate=True)
            if not 0 < len(decoded) <= MAX_MEDIA_BYTES:
                raise ValueError("Empty or oversized audio payload")
        except (ValueError, TypeError, KeyError, binascii.Error):
            self.audio_metrics.invalid_messages += 1
            return None
        frame = await super().deserialize(data)
        if not isinstance(frame, InputAudioRawFrame):
            return frame
        if len(frame.audio) % 2 or frame.num_channels != 1:
            self.audio_metrics.pcm_alignment_errors += 1
            return None
        self.audio_metrics.sequence_gaps += max(0, chunk - self._last_chunk - 1)
        self._last_message, self._last_chunk = sequence, chunk
        self._last_timestamp = int(timestamp)
        self.audio_metrics.accepted_media += 1
        self.audio_metrics.decoded_pcm_bytes += len(frame.audio)
        return frame
