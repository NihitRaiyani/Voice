"""The post-call job: what the pipeline hands the worker when a call ends (docs/09).

docs/06 specifies `{call_sid, recording_ref, locked_slot}`; docs/09 requires the stored
recording to carry `{call_sid, timestamp, duration, outcome, locked_slot}` alongside it.
The job is their union, and it has to be — the worker cannot derive the extra three from
anywhere else. `CallState` has no timestamp, no duration and no outcome field, and its
Redis checkpoint expires four hours after the call (docs/06), so by the time a backed-up
worker gets to a job the state key may be gone. Whatever the sidecar needs, the producer
must carry.

`CallState` is deliberately NOT extended to hold them: `to_dict()` is the checkpoint
schema, checkpoints are written on durable events only, a live `duration` would force a
write per tick, and `outcome` is not an input to any phase transition — `machine.py` stays
a pure function of conversation signals.

**No PII.** No phone number, no lead name, no transcript. `call_sid` is a Twilio
identifier and `locked_slot` is a time-of-day string; both are already logged elsewhere.
"""

import json
from dataclasses import dataclass, field

JOB_VERSION = 1

OUTCOME_LOCKED = "locked"
OUTCOME_NO_LOCK = "no_lock"
OUTCOME_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PostcallJob:
    """One recording awaiting storage.

    `recording_ref` is RELATIVE to `paths.media_dir()`, never absolute: relocating the
    data root then becomes a config change rather than a rewrite of every queued job.
    """

    call_sid: str
    recording_ref: str
    locked_slot: "str | None"
    started_at: str
    ended_at: str
    duration_secs: float
    audio_secs: float
    outcome: str
    sample_rate: int = 8000
    num_channels: int = 2
    attempts: int = 0

    raw: "str | None" = field(default=None, compare=False, repr=False)

    def to_dict(self) -> dict:
        return {
            "v": JOB_VERSION,
            "call_sid": self.call_sid,
            "recording_ref": self.recording_ref,
            "locked_slot": self.locked_slot,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_secs": self.duration_secs,
            "audio_secs": self.audio_secs,
            "outcome": self.outcome,
            "sample_rate": self.sample_rate,
            "num_channels": self.num_channels,
            "attempts": self.attempts,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_raw(cls, raw: str) -> "PostcallJob":
        """Parse a queued payload, keeping `raw` verbatim for the ack."""
        d = json.loads(raw)
        return cls(
            call_sid=d["call_sid"],
            recording_ref=d["recording_ref"],
            locked_slot=d.get("locked_slot"),
            started_at=d["started_at"],
            ended_at=d["ended_at"],
            duration_secs=float(d.get("duration_secs", 0.0)),
            audio_secs=float(d.get("audio_secs", 0.0)),
            outcome=d.get("outcome", OUTCOME_UNKNOWN),
            sample_rate=int(d.get("sample_rate", 8000)),
            num_channels=int(d.get("num_channels", 2)),
            attempts=int(d.get("attempts", 0)),
            raw=raw,
        )

    def with_attempt(self) -> "PostcallJob":
        """A copy with the retry counter bumped. `raw` is dropped: the payload changed, so
        the old string is no longer the thing sitting in the inflight list."""
        d = self.to_dict()
        d["attempts"] = self.attempts + 1
        return PostcallJob.from_raw(json.dumps(d, sort_keys=True, ensure_ascii=False))

    def sidecar(self) -> dict:
        """The metadata written next to the recording — exactly docs/09's list, plus a
        version. Nothing more in v1: no transcript, no name, no phone."""
        return {
            "v": JOB_VERSION,
            "call_sid": self.call_sid,
            "timestamp": self.started_at,
            "duration": self.duration_secs,
            "outcome": self.outcome,
            "locked_slot": self.locked_slot,
        }


def outcome_for(state) -> str:
    """Classify how the call ended, from the call state. Pure; never raises.

    Only three values in v1, because only one distinction actually matters downstream:
    did we lock a specific day+time (the win condition, docs/03) or not. A soft
    "dekhta hoon" is `no_lock`, deliberately — CLAUDE.md is explicit that it is not a win.
    """
    if state is None:
        return OUTCOME_UNKNOWN
    return OUTCOME_LOCKED if getattr(state, "locked_slot", None) else OUTCOME_NO_LOCK


__all__ = [
    "PostcallJob",
    "outcome_for",
    "JOB_VERSION",
    "OUTCOME_LOCKED",
    "OUTCOME_NO_LOCK",
    "OUTCOME_UNKNOWN",
]
