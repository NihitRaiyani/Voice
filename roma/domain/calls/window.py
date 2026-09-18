"""Calling-window compliance (docs/07 §consent — TRAI calling-window rules).

A commercial outbound call may only be placed within India's permitted
telemarketing window. We judge every call in IST regardless of the server's
clock, so an IST host and a UTC container agree.

The window is documented module constants, NOT env/Settings: a regulatory value
should not be casually overridden per-environment, and keeping it out of Settings
leaves the secrets/`.env.example` test surface untouched.

TODO(weltec-compliance): confirm the exact TRAI window against Weltec's
compliance source. 09:00–21:00 IST is the standard telemarketing window; treated
here as [start, end) — 09:00 is inside, 21:00 is outside.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

CALL_TIMEZONE = ZoneInfo(
    "Asia/Kolkata"
)  # tells to use the timezone of India for the calling window
CALL_WINDOW_START_HOUR = 9
CALL_WINDOW_END_HOUR = 21


def in_calling_window(now: datetime) -> bool:
    """True if `now`, expressed in IST, falls within the permitted window.

    A tz-aware `now` is converted to IST; a naive `now` is assumed to already be
    IST (caller's responsibility) so tests can pass simple wall-clock times.
    """
    ist = now.astimezone(CALL_TIMEZONE) if now.tzinfo is not None else now
    return CALL_WINDOW_START_HOUR <= ist.hour < CALL_WINDOW_END_HOUR


__all__ = [
    "in_calling_window",
    "CALL_TIMEZONE",
    "CALL_WINDOW_START_HOUR",
    "CALL_WINDOW_END_HOUR",
]
