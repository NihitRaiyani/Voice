"""Outbound edge cases found while rebuilding P1, each with the resolution that ships.

The pivot moved Roma from answering to dialling, and the first eight seconds invert with it.
Every case here is one where the call still CONNECTS and still sounds plausible while being
wrong — which is the failure shape this repo produces over and over (`vad_analyzer` dropped
silently, stream lifecycle mishandled, `OpeningTurnGuard` never opening, the flat
`VAD_STOP_SECS` after a flag flip). A green suite and a working call are not the same thing.
"""

import asyncio

import pytest
from roma.domain.conversation.state import CallState
from roma.domain.conversation.turn import defers_the_call
from roma.repositories.redis.leads import OutboundLead

# --- EDGE 1: the CRM record has no name --------------------------------------
# RESOLUTION: omit the key from the seed so CallState's None default stands, and let P1's
# fallback wording carry the greeting. Covered end to end in test_outbound_trigger and
# test_state; asserted here as the *conversational* consequence.


def test_a_nameless_lead_still_gets_asked_for_a_name():
    state = CallState(call_sid="c", **OutboundLead(phone="+91").as_state_seed())
    assert state.next_discovery_slot() == "lead_name"


# --- EDGE 2: Redis is down on a call we placed --------------------------------
# RESOLUTION: direction is keyed on the TOKEN, never on the record. A lookup failure costs
# the dynamic variables; it must not silently turn the call back into an inbound-shaped one,
# which would have Roma open with "Hello, Weltec Institute" over a stranger's "hello?".


def test_direction_is_decided_by_the_token_not_by_the_record(monkeypatch):
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+16295550100")
    monkeypatch.setenv("SARVAM_API_KEY", "s")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://host.example")
    from roma.core.config import get_settings

    get_settings.cache_clear()
    from roma.realtime.pipeline import _load_triggered_lead, build_media_app

    app = build_media_app(auto_hang_up=False)

    class _Broken:
        async def get(self, token):
            raise RuntimeError("redis down")

    app.state.lead_store = _Broken()

    # The record is gone...
    assert asyncio.run(_load_triggered_lead(app, "tok")) is None
    # ...but the token is what the /ws handler reads for `is_outbound`, and it is still there.
    assert bool("tok") is True
    get_settings.cache_clear()


# --- EDGE 3: "not now" is substantive, and used to mean "advance" -------------
# RESOLUTION: `defers_the_call` holds P1. Inbound had no such answer — a caller who rang us
# never says "I'm busy" — so `opened_the_conversation` returning True for it was harmless
# then and walks a driving lead into five discovery questions now.


DEFERRALS = [
    "abhi busy hoon",
    "main meeting mein hoon",
    "baad mein call karo",
    "driving mein hoon abhi",
    "office mein hoon, baad mein",
    "abhi nahi ho payega",
    "હું અત્યારે વ્યસ્ત છું",  # Gujarati: STT returns Gujarati script (D3)
    "અત્યારે નહીં",
]


@pytest.mark.parametrize("text", DEFERRALS)
def test_a_deferral_is_recognised_and_holds_the_opening_phase(text):
    assert defers_the_call(text), f"deferral not detected: {text!r}"


AGREEMENTS = [
    "haan boliye",
    "ji bataiye",
    "haan abhi baat karte hain",  # `abhi` alone must NOT read as a deferral
    "course ke baare mein batao",
    "kitne mahine ka course hai",
    "હા બોલો",
]


@pytest.mark.parametrize("text", AGREEMENTS)
def test_agreement_is_not_mistaken_for_a_deferral(text):
    assert not defers_the_call(text), f"over-matched as a deferral: {text!r}"


def test_the_opening_phase_does_not_advance_on_a_deferral():
    """The signal that matters: a busy lead must not land in discovery."""
    from roma.domain.conversation.turn import (
        asks_who_we_are,
        is_affirmation,
        opened_the_conversation,
    )

    text = "abhi meeting mein hoon, baad mein call karna"
    # It IS substantive — which is exactly why the inbound rule alone would have advanced.
    assert opened_the_conversation(text)
    assert not is_affirmation(text)
    assert not asks_who_we_are(text)
    # ...and the new gate stops it.
    inquiry_confirmed = (
        not asks_who_we_are(text)
        and not defers_the_call(text)
        and (is_affirmation(text) or opened_the_conversation(text))
    )
    assert not inquiry_confirmed


# --- EDGE 4: a resumed call must not be re-seeded ------------------------------
# RESOLUTION: the lead is applied only when `store.load` returned None. A reconnect
# mid-call would otherwise overwrite a name the lead corrected ("Nihit nahi, Nihit Raiyani")
# with the stale one the CRM had, on the turn right after they corrected it.


def test_a_corrected_name_is_not_overwritten_by_the_crm_record():
    lead = OutboundLead(phone="+91", lead_name="Nihit", city="Vadodara")
    resumed = CallState(call_sid="c", lead_name="Nihit Raiyani", city="Anand")

    # What the /ws handler does NOT do on a resumed state:
    would_have = dict(lead.as_state_seed())
    assert would_have["branch"] == "Vadodara"
    assert resumed.lead_name == "Nihit Raiyani", "resume must win over the CRM record"
    assert resumed.city == "Anand"


# --- EDGE 5: segment is a variable, not a branch --------------------------------
# RESOLUTION: validated at construction so a typo fails at the trigger rather than becoming
# a prompt variable that silently means nothing for the whole call.


def test_an_unknown_segment_cannot_reach_a_call():
    with pytest.raises(ValueError, match="segment"):
        OutboundLead(phone="+91", segment="fresher")


def test_a_missing_segment_is_valid_and_renders_empty():
    state = CallState(call_sid="c", **OutboundLead(phone="+91").as_state_seed())
    assert state.segment == ""
    assert state.as_prompt_vars()["segment"] == ""
