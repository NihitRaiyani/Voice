"""Transcript observability for Step 2 (docs/10: "verify transcripts arrive").

The STT stage (Sarvam Saaras) emits a `TranscriptionFrame` once the turn is finalized.
This processor sits right after STT in the pipeline, logs it at INFO and tallies it —
the Step-2 proof that real transcripts flow, mirroring how `InboundAudioCounter` proved
audio-IN in Step 1. It forwards every frame untouched so downstream stages (LLM in
Step 3) build on the same shape.

**Sarvam emits no interims.** `SarvamSTTService` never constructs an
`InterimTranscriptionFrame`, and it only flushes its socket on
`VADUserStoppedSpeakingFrame` — so `interim_count` is always 0 on this stack, and
first-token latency is gated by Sarvam's p99 TTFS (pipecat's own figure: 1.17s). That
is the measurement behind Step 5B's two-armed barge-in guard: a transcript-gated
interruption would fire ~1.2-2.1s after speech onset, long after the lead stopped
talking. The interim branch below is kept as a defensive no-op — a different STT, or a
Sarvam release that starts streaming partials, must be counted, not dropped silently.
"""

import logging

from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_log = logging.getLogger("roma.telephony")


class TranscriptionLogger(FrameProcessor):
    """Log + count STT transcripts, passing every frame through unchanged.

    **Step 7 timing does NOT live here — see `media._UsageLogger`.** An earlier version of
    this class timed `VADUserStoppedSpeakingFrame` -> `TranscriptionFrame` and recorded
    nothing at all on two live calls (`stt_finalize={}`): that frame is consumed by the turn
    controller and never travels down the pipeline this far. It was also redundant, because
    pipecat already emits TTFB for every service as a `MetricsFrame`. Both mistakes are
    worth remembering — an instrument that silently measures nothing is worse than none, and
    re-measuring what the framework measures produces two numbers that disagree.
    """

    def __init__(self, flight=None) -> None:
        super().__init__()
        self._flight = flight
        self.interim_count = 0
        self.final_count = 0
        self.finals: list[str] = []
        self.last_final: str | None = None

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            self.final_count += 1
            if self._flight is not None:
                self._flight.user_stopped()
            self.last_final = frame.text
            self.finals.append(frame.text)
            _log.info("transcript final #%d (%d chars)", self.final_count, len(frame.text))
            _log.debug("transcript final: %r", frame.text)
        elif isinstance(frame, InterimTranscriptionFrame):
            self.interim_count += 1
            _log.debug("transcript interim: %r", frame.text)
        await self.push_frame(frame, direction)


__all__ = ["TranscriptionLogger"]
