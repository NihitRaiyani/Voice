"""Provider-independent facts a completed call sends to background processing."""

from typing import Protocol

OUTCOME_LOCKED = "locked"
OUTCOME_NO_LOCK = "no_lock"
OUTCOME_UNKNOWN = "unknown"


class CompletedCall(Protocol):
    call_sid: str
    started_at: str
    ended_at: str
    duration_secs: float
    audio_secs: float
    turn_count: int
    outcome: str
    direction: str
    final_stage: str | None
    locked_slot: str | None
    recording_ref: str | None
    caller_digest: str | None
