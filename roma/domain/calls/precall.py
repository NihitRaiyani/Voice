"""The pre-dial gate (docs/07 §consent, docs/10 Gate 0).

The Step-1 dialer MUST call `precall_check` and dial ONLY on `may_dial=True`,
speaking `consent_line` first. This mirrors how the TTS path must go through the
pre-TTS filter (roma/domain/safety/filter.py): a standalone, deterministic gate
that never raises and, on any internal error, fail-safe BLOCKs the call rather
than proceeding unguarded (docs/07 fail-safe posture).

No Twilio API here — the actual outbound-call wiring is Step 1. This is the
"stub into the dialer path": the guard is real; the dialing is deferred.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from roma.domain.calls.consent import CONSENT_LINE
from roma.domain.calls.dnd import DoNotCallRegistry
from roma.domain.calls.window import in_calling_window

_log = logging.getLogger("roma.domain.calls")


@dataclass(frozen=True)
class PrecallVerdict:
    may_dial: bool
    reason: str
    consent_line: "str | None"


def _block(reason: str) -> PrecallVerdict:
    return PrecallVerdict(may_dial=False, reason=reason, consent_line=None)


def precall_check(
    phone: str,
    now: datetime,
    registry: DoNotCallRegistry,
    *,
    spend=None,
    budget_inr: "float | None" = None,
) -> PrecallVerdict:
    """Decide whether an outbound call to `phone` may be placed at `now`.

    Order: calling-window → DND/allowlist → budget. Any internal error fail-safe BLOCKs
    (never proceeds unguarded). Never raises.

    `spend` is a `roma.domain.costs.spend.SpendLedger` and `budget_inr` the testing-phase cap; when
    either is omitted the money gate is simply not applied, which is right for the unit
    tests and for any caller that has already decided the call is paid for. The gate runs
    LAST on purpose: a DND block must never be masked by a budget block, because topping
    up the budget must not turn a non-dialable number into a dialable one.

    Why there is a money gate here at all: gpt-4o costs ~16.7x gpt-4o-mini
    (`roma.domain.costs.spend.PRICES`), which makes a five-minute call roughly ₹8 against a ₹100
    testing phase. The pre-dial gate is the only place that can refuse cleanly — a budget
    check inside the call would have to hang up on a lead mid-sentence.
    """
    try:
        if not in_calling_window(now):
            return _block("block:window")
        if not registry.is_dialable(phone):
            return _block("block:dnd")
        if spend is not None and budget_inr is not None and spend.exhausted(budget_inr):
            _log.error(
                "pre-dial gate: OpenAI testing budget of ₹%.2f is spent. Raise "
                "OPENAI_BUDGET_INR (and the OpenAI dashboard limit, which is the real "
                "ceiling) or stop testing.",
                budget_inr,
            )
            return _block("block:budget")
        return PrecallVerdict(may_dial=True, reason="ok", consent_line=CONSENT_LINE)
    except Exception as exc:  # noqa: BLE001 — fail-safe, never dial unguarded
        _log.error("pre-dial gate error, blocking call: %s", type(exc).__name__)
        return _block(f"precall-error:{type(exc).__name__}")


__all__ = ["precall_check", "PrecallVerdict"]
