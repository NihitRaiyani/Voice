"""Roma pre-dial gate (docs/07 §consent, docs/10 Gate 0).

No call is placed without this. The Step-1 dialer calls `precall_check` and dials
only on `may_dial=True`, speaking `consent_line` first. Allowlist-only DND posture;
fail-safe BLOCK on any error (never dial unguarded).
"""

from roma.dialer.consent import (
    CONSENT_LINE,
    CONSENT_PENDING_MARKER,
    consent_signed_off,
)
from roma.dialer.dnd import DoNotCallRegistry, StubRegistry
from roma.dialer.precall import PrecallVerdict, precall_check

__all__ = [
    "precall_check",
    "PrecallVerdict",
    "CONSENT_LINE",
    "CONSENT_PENDING_MARKER",
    "consent_signed_off",
    "DoNotCallRegistry",
    "StubRegistry",
]
