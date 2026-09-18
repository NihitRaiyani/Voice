"""Turns whose answer is already decided, answered without a completion (docs/03, docs/04).

`pretts` sits DOWNSTREAM of the LLM, so a turn whose reply is fixed by policy still pays a
full generation before that reply overwrites it. Live call 932b6c88:

    19:03:15.370  LLM TTFB: 1.940s
    19:03:16.723  TTS: "...Visit ka time abhi tay karna baaki hai..."   <- a constant

1.9s of generation, billed, discarded. `advance_turn` had already decided the answer before
the request went out — the machine picks the phase, never the model (docs/03).

## The rule this module holds to

**A line is short-circuitable only when the answer does not depend on what the lead said.**
Not "we can guess the intent" — that is a different and much weaker claim. Three families
qualify:

1. **The lead asked us to stop.** `lead_wants_out` is already sticky on the state, and the
   only correct reply is a warm close. Getting this wrong is a compliance problem, not a
   sales one, which is exactly why it must not depend on a model's mood.
2. **A readback is pending.** `slot_status == "accepted"` means the machine chose the
   words: read the slot back. `SAFE_READBACK_HOLD_LINE` is literally a format string over
   the slot.
3. **A guarded topic with a quantity ask.** docs/04 fixes the reply to
   `SUBSTITUTIONS[category]` and the filter will force it regardless. Generating first is
   pure waste.

## What this must NOT swallow, and why the detectors are narrow

The over-reach failure is worse than the latency it saves: a router that cans too much
turns Roma into an IVR that will not discuss her own course. So family 3 needs a QUANTITY
cue, not merely the topic word:

    "Placement ka percentage kya hai?"   -> canned  (a number we may not give)
    "Placement support kaisa hota hai?"  -> the LLM (a real question with a real answer)
    "Certificate kis naam se milta hai?" -> the LLM (answerable: the Weltec certificate)
    "Google ka certificate milta hai?"   -> canned  (a third-party claim, blocked until D1)

A booking request is never short-circuited. "Meeting fix karo" needs an offer built from
this lead's constraints — `{{offered_slots}}`, `{{known}}`, the day they named — and canning
it is the exact failure the concreteness KB in `persona.md` exists to prevent.

Every line returned here comes from `confirmguard` or `guardrails.lexicon`. This module owns
no strings of its own: "Ek minute ji" reached five separate sources in this build before
anyone noticed, and a compliance sentence must not fork on whether the router fired.
"""

import logging

from roma.controller.confirmguard import (
    SAFE_READBACK_HOLD_LINE,
    SAFE_SIGNOFF_LINE,
    lead_wants_out,
)
from roma.controller.state import spoken_slot
from roma.guardrails.lexicon import SUBSTITUTIONS, BlockCategory
from roma.guardrails.normalize import tokens

_log = logging.getLogger("roma.controller")

# "How much / what number" — the cue that turns a topic question into one docs/04 answers.
QUANTITY_CUES = frozenset(
    {
        "kitni",
        "kitna",
        "kitne",
        "percentage",
        "percent",
        "ratio",
        "%",
        "कितनी",
        "कितना",
        "कितने",
        "प्रतिशत",
        "કેટલી",
        "કેટલું",
        "કેટલા",
        "ટકા",
    }
)

FEE_TOPIC = frozenset({"fees", "fee", "फीस", "ફી", "charge", "charges", "cost"})
PLACEMENT_TOPIC = frozenset({"placement", "placements", "प्लेसमेंट", "પ્લેસમેન્ટ"})
SALARY_TOPIC = frozenset({"salary", "package", "lpa", "सैलरी", "पैकेज", "સેલરી"})

# A third-party certificate claim is blocked outright until D1 is answered (docs/04 CERT),
# so it needs no quantity cue — the BRAND is the trigger. "Certificate kis naam se milta
# hai" carries no brand and is answerable: we give the Weltec certificate.
CERT_BRANDS = frozenset(
    {
        "google",
        "meta",
        "facebook",
        "ibm",
        "microsoft",
        "adobe",
        "hubspot",
        "government",
        "sarkari",
        "गूगल",
        "सरकारी",
        "ગૂગલ",
        "સરકારી",
    }
)


def _guarded_category(toks: "set[str]") -> "BlockCategory | None":
    """Which docs/04 category, if any, this question has a fixed answer for."""
    quantity = bool(toks & QUANTITY_CUES)
    if toks & CERT_BRANDS:
        return BlockCategory.CERT
    if quantity and toks & FEE_TOPIC:
        return BlockCategory.FEE
    if quantity and toks & SALARY_TOPIC:
        return BlockCategory.SALARY
    if quantity and toks & PLACEMENT_TOPIC:
        return BlockCategory.PLACEMENT
    return None


def canned_reply(state, user_text: str) -> "str | None":
    """The line for this turn if the machine already knows it, else None.

    None means "ask the model" and is the safe default: every failure path in here returns
    None, so a bug in the router costs a completion we would have paid for anyway, never a
    wrong answer or a dropped turn.
    """
    try:
        if state.lead_wants_out or lead_wants_out(user_text):
            _log.info("short-circuit: sign-off, no completion")
            return SAFE_SIGNOFF_LINE

        toks = set(tokens(user_text))

        category = _guarded_category(toks)
        if category is not None:
            _log.info("short-circuit: %s, no completion", category.name)
            return SUBSTITUTIONS[category]

        # A readback is pending only when there IS a slot to read back; `as_prompt_vars`
        # makes the same check before it will render an "accepted" status line.
        if state.slot_status == "accepted":
            slot = spoken_slot(state.accepted_slot)
            if slot:
                _log.info("short-circuit: readback, no completion")
                return SAFE_READBACK_HOLD_LINE.format(slot=slot)
        return None
    except Exception:  # noqa: BLE001 — a latency optimisation must never cost a turn
        _log.warning("short-circuit router failed; falling through to the model")
        return None


__all__ = ["canned_reply", "QUANTITY_CUES", "CERT_BRANDS"]
