"""Is a reply on its way? — one shared answer for the processors that cannot see it alone.

## The bug this exists for

Live call CA4ba2a6b8 (2026-07-28) went dead 65 seconds in. The sequence:

    16:11:32,497  transcript final #5
    16:11:37,413  silence watchdog: 5.0s with nobody speaking; restarting Roma's turn
    16:11:37,497  Generating TTS [Theek, aapne 2021 mein complete kiya. Aap rehte kahan hain?]
    16:11:51,924  OpenAILLMService#0 TTFB: 14.432s
    16:11:51,988  Generating TTS [Samjhi, aapne 2021 mein complete kiya tha. Aur aap rehte...]
    16:11:52,263  unable to append audio to context: no context ID provided   (x many)

Two generations in flight at once. The first one's TTS context was torn down while the
second was still arriving, every later audio frame was discarded for having no context, and
the lead heard nothing again for the rest of the call.

The watchdog was supposed to have stood down the moment that transcript arrived. It could
not: **`LLMUserAggregator` consumes `TranscriptionFrame` and never pushes it downstream**
(`llm_response_universal.py`, the `_handle_transcription` branch), and the watchdog sits at
the end of the pipeline. The disarm was dead code from the day it was written, and so was
the `_touch()` before it — which is why the 4-second version misfired too.

This is the third time this repo has been caught by the same thing. `_UsageLogger` documents
it about `VADUserStoppedSpeakingFrame`; the fix here has to assume nothing about which
frames survive the aggregator.

## The shape

Two processors each know half of it and neither can see the other's half:

* `PhaseControllerProcessor` sits UPSTREAM of the LLM and sees the `LLMContextFrame` that
  starts a turn. It cannot see audio.
* `SilenceWatchdog` sits after the output transport and sees `TTSAudioRawFrame` when it
  reaches the wire. It cannot see a turn begin.

So they share this object instead of guessing from frames. One writer for the start, one for
the finish, and the question "is a reply on its way?" has a single answer rather than an
inference that silently rots when a dependency changes what it forwards.

It also carries the end-to-end latency, because it is the only place that holds both ends of
the measurement — turn start to first audio out, which is the reply as the lead experiences
it.
"""

import time

MAX_PENDING_SECS = 25.0


class TurnFlight:
    """Whether Roma owes the lead a reply right now, and how long the last ones took."""

    def __init__(self, max_pending_secs: float = MAX_PENDING_SECS) -> None:
        self._started_at: float | None = None
        self._max = max_pending_secs
        self.latencies: list[float] = []
        self.heard_latencies: list[float] = []
        self._heard_at: float | None = None

    def user_stopped(self) -> None:
        """A final transcript was produced. Written from UPSTREAM of the user aggregator —
        the aggregator consumes `TranscriptionFrame` and nothing downstream ever sees one."""
        self._heard_at = time.monotonic()

    def turn_started(self) -> None:
        """A user turn has been handed to the controller; a reply is now owed."""
        self._started_at = time.monotonic()

    def first_audio(self) -> None:
        """Roma's audio reached the wire. Only the first frame of a turn records."""
        now = time.monotonic()
        if self._started_at is not None:
            self.latencies.append(now - self._started_at)
            self._started_at = None
        if self._heard_at is not None:
            self.heard_latencies.append(now - self._heard_at)
            self._heard_at = None

    @property
    def pending(self) -> bool:
        """True while a reply is genuinely on its way.

        Reading this expires a turn that has outlived `MAX_PENDING_SECS`, so a generation
        that died without producing audio cannot silence the watchdog permanently.
        """
        if self._started_at is None:
            return False
        if time.monotonic() - self._started_at > self._max:
            self._started_at = None
            return False
        return True

    @staticmethod
    def _summary(samples: "list[float]") -> dict:
        """n/p50/max, or {} when nothing was measured.

        No p95. Nearest-rank p95 needs ~40 samples before it stops returning the last index,
        and no call has that many turns — on CA00417672's n=17 the "p95" WAS the max, so the
        field claimed a tail statistic while reporting a single slowest turn. A real p95 needs
        aggregation across calls, which is a time-series store's job, not this object's.
        """
        if not samples:
            return {}
        o = sorted(samples)
        return {
            "n": len(o),
            "p50": round(o[len(o) // 2], 3),
            "max": round(o[-1], 3),
        }

    def summary(self) -> dict:
        """Controller-to-audio: the span this process controls."""
        return self._summary(self.latencies)

    def heard_summary(self) -> dict:
        """Last-word-to-audio: the span the LEAD experiences. Always the larger of the two."""
        return self._summary(self.heard_latencies)


__all__ = ["TurnFlight", "MAX_PENDING_SECS"]
