"""Notice when a service on the call has died, and say so.

## The call this exists to stop

CA34ca96e (2026-07-27). The lead answered and heard Roma greet them, and then nothing —
they talked into a line that could not hear them, and hung up. From their side the call
"got cut instantly". The whole cause is one line, eleven seconds in:

    09:16:02  ERROR  SarvamSTTService#0 exception (asyncio/timeouts.py:115):
                     Failed to connect to Sarvam:
    09:16:02  WARNING PipelineTask#0: Something went wrong: ErrorFrame#0(..., fatal: False)
    09:16:12  DEBUG  SarvamTTSService#0: Generating TTS [Hi ji, Roma baat kar rahi hoon...]

The STT websocket never opened. TTS did, so Roma spoke; nothing could carry the lead's
voice back. And `SarvamSTTService._connect` handles a connect failure by pushing a
non-fatal `ErrorFrame` and returning — pipecat's `WebsocketService` reconnect machinery
covers a socket that DROPS, not one that never came up. So the service stays dead for the
rest of the call, and the only trace is a DEBUG-level line in a 300 KB log.

## What this does about it

Two things, and deliberately not a third.

It **reports**: our own logger gets a WARNING per service error, and the teardown line
carries the count, so a deaf call can never again look like an ordinary short one.

It **ends the call**: `CallCloser` reads `deaf` and hangs up on Roma's next natural pause.
A lead on a line that cannot hear them is worse than a dropped call — they repeat
themselves, get nothing, and conclude the company is broken. Ending it lets them redial
and lets the dialer try again.

It does **not** retry the connect. `_connect()` is private to the Sarvam service and
reaching into it to re-drive the handshake would couple us to pipecat's internals on the
single most timing-sensitive path in the system. The connect failure is also a symptom
rather than a cause: this box measures a median 1544 ms TCP RTT to the same class of
endpoint with ~1 s SYN retransmissions (`scripts/rtt_mumbai.py`), and a retry loop over a
lossy link mostly buys a longer silence before the same outcome. Fix the network, and keep
the failure loud in the meantime.
"""

import logging
from dataclasses import dataclass, field

from pipecat.services.stt_service import STTService

_log = logging.getLogger("roma.realtime")


@dataclass
class CallHealth:
    """Service-level failures seen on one call.

    One instance per connection, held in `CallHandles` alongside the other per-call
    observability (docs/08) — never a process-global, or concurrent calls would report each
    other's failures.
    """

    errors: list = field(default_factory=list)
    stt_failed: bool = False
    degraded: dict = field(default_factory=dict)

    def record(self, frame) -> None:
        """Handle one `ErrorFrame` from `on_pipeline_error`. Never raises.

        The originating processor comes from `ErrorFrame.processor`, so STT is identified
        by TYPE rather than by matching on the message text — a service whose wording
        changes must not silently stop being recognised as the STT.
        """
        try:
            processor = getattr(frame, "processor", None)
            text = str(getattr(frame, "error", "") or "")
            self.errors.append(text)
            if isinstance(processor, STTService):
                self.stt_failed = True
                _log.error(
                    "STT service error — the call cannot hear the lead: %s",
                    text or "(no detail)",
                )
            else:
                _log.warning("service error on %s: %s", type(processor).__name__, text)
        except Exception:  # noqa: BLE001 — health reporting must never break the call
            _log.warning("could not record a pipeline error")

    def degrade(self, site: str) -> None:
        """Record that `site` swallowed an exception and the call continued. Never raises.

        Call it from the `except` branch, next to the log line. `site` is a short stable
        slug, not the exception text — the count is what makes a degraded call visible in
        teardown, and the detail is already in the log entry beside it.
        """
        try:
            self.degraded[site] = self.degraded.get(site, 0) + 1
        except Exception:  # noqa: BLE001 — a counter must never be the thing that fails
            pass

    @property
    def deaf(self) -> bool:
        """True when nothing the lead says can reach the machine, so the call is over.

        Deliberately not "any error": a TTS hiccup is recoverable and a filler-clip failure
        is cosmetic. Only the STT being down makes the conversation structurally impossible.
        """
        return self.stt_failed


__all__ = ["CallHealth"]
