"""Deterministic endpoint decisions over scored 32 ms, 16 kHz mono PCM16 frames.

The model supplies probabilities only. Timers use received audio duration, never
wall time. A transport gap must call discontinuity() (and reset its scorer); it
is not counted as silence or bridged with invented samples.
"""

import math
from collections import deque
from dataclasses import dataclass
from numbers import Real
from typing import Literal

from roma.domain.audio.pcm import AudioFormatError
from roma.domain.audio.turn_policy import TurnPolicy

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_MS = 32

EndReason = Literal["silence", "max_duration", "flush", "discontinuity", "deadline"]


def validate_frame(
    pcm: bytes, *, sample_rate: int = SAMPLE_RATE, channels: int = 1, sample_width: int = 2
) -> None:
    """Require explicit little-endian signed PCM16 metadata and one complete frame."""
    if (
        type(sample_rate) is not int
        or sample_rate != SAMPLE_RATE
        or type(channels) is not int
        or channels != 1
        or type(sample_width) is not int
        or sample_width != 2
        or not isinstance(pcm, bytes)
        or len(pcm) != FRAME_BYTES
    ):
        raise AudioFormatError("Expected 512 samples of 16 kHz mono signed little-endian PCM16")


def validate_probability(probability: float) -> None:
    if (
        isinstance(probability, bool)
        or not isinstance(probability, Real)
        or not math.isfinite(probability)
        or not 0 <= probability <= 1
    ):
        raise AudioFormatError("Speech probability must be finite and between zero and one")


@dataclass(frozen=True)
class EndpointConfig:
    speech_threshold: float = 0.6
    silence_threshold: float = 0.35
    start_ms: int = 64
    pre_roll_ms: int = 192
    hangover_ms: int = 128
    silence_ms: int = TurnPolicy().endpoint_silence_ms
    max_utterance_ms: int = TurnPolicy().max_utterance_ms

    def __post_init__(self) -> None:
        validate_probability(self.speech_threshold)
        validate_probability(self.silence_threshold)
        if not 0 < self.silence_threshold < self.speech_threshold <= 1:
            raise AudioFormatError("Use positive silence threshold below speech threshold")
        durations = (
            self.start_ms,
            self.pre_roll_ms,
            self.hangover_ms,
            self.silence_ms,
            self.max_utterance_ms,
        )
        if any(type(value) is not int or not 0 <= value <= 120000 for value in durations):
            raise AudioFormatError(
                "Endpoint durations must be integer milliseconds in 0..120000"
            )
        if (
            self.start_ms == 0
            or self.silence_ms == 0
            or self.hangover_ms > self.silence_ms
            or self.max_utterance_ms // FRAME_MS
            <= self.frames(self.pre_roll_ms) + self.frames(self.start_ms)
        ):
            raise AudioFormatError("Require start/silence duration and room for bounded speech")

    @staticmethod
    def frames(duration_ms: int) -> int:
        """Round waits/retention up to the next complete model frame."""
        return math.ceil(duration_ms / FRAME_MS)


@dataclass(frozen=True)
class Utterance:
    """Bounded ASR chunk, or an empty final boundary after an exact maximum split.

    Only reasons other than max_duration finalize a caller turn. An empty PCM
    boundary finalizes previously emitted chunks and must never be sent to ASR.
    """

    pcm: bytes
    start_sample: int
    end_sample: int
    speech_frames: int
    reason: EndReason
    last_speech_sample: int | None = None

    @property
    def duration_ms(self) -> float:
        return (self.end_sample - self.start_sample) * 1000 / SAMPLE_RATE


class EndpointDetector:
    """One stream, bounded pre-roll and utterance storage; no minimum-length veto.

    Two high frames normally confirm onset. Once started, probabilities at or
    above the lower threshold retain speech and reset the silence timer. This
    keeps quiet trailing words and short acknowledgements. Explicit flush/gap
    also preserves a final unconfirmed high frame. Configured durations are
    rounded up to frames, except the maximum which is rounded down (a hard cap).
    """

    def __init__(self, config: EndpointConfig | None = None) -> None:
        self.config = config or EndpointConfig()
        self._start_frames = self.config.frames(self.config.start_ms)
        self._hangover_frames = self.config.frames(self.config.hangover_ms)
        self._silence_frames = self.config.frames(self.config.silence_ms)
        self._max_frames = self.config.max_utterance_ms // FRAME_MS
        self._pre: deque[tuple[int, bytes]] = deque(
            maxlen=self.config.frames(self.config.pre_roll_ms) + self._start_frames
        )
        self._audio: list[bytes] = []
        self._position = 0
        self._clear_segment()

    @property
    def state(self) -> Literal["idle", "starting", "speaking", "ending"]:
        if self._audio or self._continuing:
            return "ending" if self._silent else "speaking"
        return "starting" if self._candidate else "idle"

    @property
    def buffered_bytes(self) -> int:
        return (len(self._pre) + len(self._audio)) * FRAME_BYTES

    @property
    def position_samples(self) -> int:
        return self._position

    def _clear_segment(self) -> None:
        self._pre.clear()
        self._audio.clear()
        self._candidate = 0
        self._continuing = False
        self._silent = 0
        self._speech_frames = 0
        self._last_speech = 0
        self._start_sample = self._position

    def _begin(self) -> None:
        # A flush can promote fewer than start_frames; retain only its pre-roll.
        retained = list(self._pre)[
            -(self.config.frames(self.config.pre_roll_ms) + self._candidate) :
        ]
        self._start_sample = retained[0][0]
        self._audio = [pcm for _, pcm in retained]
        self._pre.clear()
        self._speech_frames = self._candidate
        self._last_speech = len(self._audio)
        self._candidate = 0
        self._continuing = False
        self._silent = 0

    def _emit(self, reason: EndReason) -> Utterance:
        end = min(len(self._audio), self._last_speech + self._hangover_frames)
        pcm = b"".join(self._audio[:end])
        utterance = Utterance(
            pcm,
            self._start_sample,
            self._start_sample + end * FRAME_SAMPLES,
            self._speech_frames,
            reason,
            self._start_sample + self._last_speech * FRAME_SAMPLES if self._last_speech else None,
        )
        silent = self._silent
        self._clear_segment()
        if reason == "max_duration":
            # An ASR size boundary is not an acoustic end of speech. Keep the
            # lower continuation threshold and elapsed silence until real EOT.
            self._continuing = True
            self._silent = silent
        return utterance

    def feed(
        self,
        pcm: bytes,
        probability: float,
        *,
        sample_rate: int = SAMPLE_RATE,
        channels: int = 1,
        sample_width: int = 2,
        discontinuity: bool = False,
    ) -> tuple[Utterance, ...]:
        # Reject invalid frames before any timer, gap flush or buffer mutation.
        validate_frame(
            pcm, sample_rate=sample_rate, channels=channels, sample_width=sample_width
        )
        validate_probability(probability)
        if type(discontinuity) is not bool:
            raise AudioFormatError("Discontinuity must be boolean")
        output = list(self.discontinuity()) if discontinuity else []
        offset = self._position
        self._position += FRAME_SAMPLES
        if not self._audio:
            self._pre.append((offset, pcm))
            if self._continuing:
                if probability >= self.config.silence_threshold:
                    self._candidate = 1
                    self._begin()
                else:
                    self._silent += 1
                    if self._silent >= self._silence_frames:
                        output.append(self._emit("silence"))
            else:
                self._candidate = (
                    self._candidate + 1 if probability >= self.config.speech_threshold else 0
                )
                if self._candidate >= self._start_frames:
                    self._begin()
        else:
            self._audio.append(pcm)
            if probability >= self.config.silence_threshold:
                self._silent = 0
                self._speech_frames += 1
                self._last_speech = len(self._audio)
            else:
                self._silent += 1
        if self._audio:
            if self._silent >= self._silence_frames:
                output.append(self._emit("silence"))
            elif len(self._audio) >= self._max_frames:
                output.append(self._emit("max_duration"))
        return tuple(output)

    def _finish(self, reason: EndReason) -> tuple[Utterance, ...]:
        if not self._audio and self._candidate:
            self._begin()
        if self._audio or self._continuing:
            return (self._emit(reason),)
        self._clear_segment()
        return ()

    def flush(self, *, reason: Literal["flush", "deadline"] = "flush") -> tuple[Utterance, ...]:
        """Finish available speech without inventing silence; idempotent."""
        if reason not in {"flush", "deadline"}:
            raise ValueError("Invalid endpoint flush reason")
        return self._finish(reason)

    def discontinuity(self) -> tuple[Utterance, ...]:
        """Finish the old segment and discard all onset/hysteresis history."""
        return self._finish("discontinuity")

    def reset(self) -> None:
        """Discard all retained audio and restart the sample clock for a new stream."""
        self._position = 0
        self._clear_segment()
