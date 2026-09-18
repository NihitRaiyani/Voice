"""Which turns need the model, and which are already decided (docs/03, docs/04).

PART GREEN, PART RED BY DESIGN. The routing predicates exist and are tested here as they
are today; the short-circuit that would act on them does not exist yet, and those tests
fail with an assertion naming it.

## The measurement behind this

`pretts` sits DOWNSTREAM of `llm` in the pipeline (`media.py:1300-1303`), so a turn whose
answer is a fixed string still pays a full completion before that string replaces it. From
live call 932b6c88:

    19:03:15.370  LLM TTFB: 1.940s
    19:03:16.723  TTS: "...Visit ka time abhi tay karna baaki hai..."   <- canned constant

1.9 seconds of generation, billed, then discarded and replaced by `SAFE_HOLD_LINE`. The
deterministic layer already KNEW the answer before the request went out — `advance_turn`
runs ahead of the LLM, by design, so that the machine picks the phase and never the model.

`PickupGreeter` already proves the mechanism: push `LLMFullResponseStartFrame` ->
`TextFrame` -> `LLMFullResponseEndFrame` and the rest of the pipeline cannot tell the
difference (`opening.py:412-415`). The greeting takes that path today. Nothing else does.

## Why this is 5 / 3 / 2 and not the 5 / 5 it looks like

A booking request is NOT short-circuitable, and lumping it in with the objections is the
easy mistake here. "Meeting fix karo" needs a phrased offer built from the lead's own
constraints — `{{offered_slots}}`, `{{known}}`, the day they named. Canning it would make
Roma answer a booking request with a form letter, which is the exact failure the concreteness
KB in `persona.md` exists to prevent.

So the honest split of ten utterances:

  * **5 on-topic** — need the model. Green today, and pins that a future short-circuit does
    not swallow them.
  * **3 bypass-eligible** — the answer is a constant that already exists in `confirmguard`.
    Red today.
  * **2 booking-intent** — route to P5 *and* still reach the model. Green today, and the
    guard against a short-circuit that over-reaches.
"""

import asyncio
from datetime import datetime

from roma.controller.confirmguard import (
    SAFE_HOLD_LINE,
    lead_wants_out,
)
from roma.controller.machine import P5_PIVOT
from roma.controller.slots import DiscoveryValue, TimeSlot
from roma.controller.state import CallState
from roma.controller.timeresolve import IST
from roma.controller.turn import advance_turn, defers_the_call, wants_to_book
from roma.guardrails import safe_output
from roma.guardrails.filter import screen
from roma.guardrails.lexicon import SUBSTITUTIONS, BlockCategory

# Frozen like `test_turn.py:19` — a Friday morning, inside visiting hours, so the offer
# phase has real slots to name and the test does not drift with the wall clock.
NOW = datetime(2026, 7, 25, 8, 0, tzinfo=IST)

# --- the dataset -------------------------------------------------------------------------

ON_TOPIC = [
    "Course kitne mahine ka hai?",
    "Placement ke baare mein bataiye",
    "Kya weekend batch available hai?",
    "Aapka institute kahan hai?",
    "Main abhi college mein hoon, third year",
]

BYPASS_ELIGIBLE = [
    "Mujhe interest nahi hai, call mat karo",
    "Pareshan mat karo, phone rakho",
    "Bhai rehne do, baat nahi karni",
]

BOOKING_INTENT = [
    "Meeting fix karo",
    "Main visit karna chahta hoon",
]


def _state(**kw):
    return CallState(branch="Vadodara", call_sid="CAtest", **kw)


class _SpyClient:
    """Counts completions. `advance_turn` takes a client for slot extraction, which is a
    SEPARATE concern from the pipeline LLM — this only proves the routing decision itself
    costs no generation."""

    def __init__(self):
        self.calls = 0


async def _fake_discovery(client, text, slot_name):
    """Async and low-confidence on purpose (`advance_turn` awaits both extractors).

    Below the acceptance threshold, so no discovery slot fills and no time is accepted:
    the ROUTING decision stays the only thing that can move the phase, which is what this
    file is about.
    """
    return DiscoveryValue(value=text, confidence=0.2)


async def _fake_time(client, text, *, offered=None):
    return TimeSlot(confidence=0.0)


def _advance(state, text):
    return asyncio.run(
        advance_turn(
            state,
            text,
            client=_SpyClient(),
            now=NOW,
            extract_discovery=_fake_discovery,
            extract_time=_fake_time,
        )
    )


# --- green: the predicates, as they behave today -----------------------------------------


def test_on_topic_utterances_are_not_mistaken_for_an_exit():
    """The false-positive direction, and the one that costs a lead.

    A wrong `lead_wants_out` makes Roma sign off on someone asking about placements.
    """
    for text in ON_TOPIC:
        assert not lead_wants_out(text), f"{text!r} was read as the lead asking to leave"
        assert not wants_to_book(text), f"{text!r} was read as a booking request"


def test_every_bypass_eligible_utterance_is_recognised_deterministically():
    for text in BYPASS_ELIGIBLE:
        assert lead_wants_out(text), (
            f"{text!r} must be caught by the lexicon, not the model — it is the one class "
            "of turn where a wrong answer is a compliance problem, not a sales one"
        )


def test_booking_intent_outranks_the_deferral_lexicon_it_collides_with():
    """Meeting fix karo" matches BOTH detectors, and that is not a bug to remove.

    `meeting` legitimately means "book me one" and "I am in one", so
    `defers_the_call("Meeting fix karo")` is True and must stay True — the word really is a
    deferral cue. What matters is precedence: an EXPLICIT `wants_to_book` wins outright, and
    `defers_the_call` only vetoes the weak route (two repeated time questions). An earlier
    version wired the veto ahead of the explicit signal and the pivot killed itself.
    """
    for text in BOOKING_INTENT:
        assert wants_to_book(text), f"{text!r} is a booking request"
    assert defers_the_call("Meeting fix karo"), (
        "the collision is real and this test is only meaningful while it exists"
    )


def test_booking_intent_routes_to_the_offer_phase():
    """The precedence above, proven end to end rather than asserted about the predicates."""
    for text in BOOKING_INTENT:
        for start in ("p2_discover", "p3_value", "p4_structure", "p6_objection"):
            state = _state(phase=start)
            _advance(state, text)
            assert state.phase == P5_PIVOT, (
                f"{text!r} from {start} did not reach the offer phase — intent must "
                "outrank phase order"
            )


def test_a_booking_request_answering_the_opener_is_swallowed_by_declined_now():
    """RED — found while writing this file, not previously known.

    `machine.next_phase` lists P1_OPEN among the phases a booking request may pivot from
    (`machine.py:88-93`), but the pivot is gated on `not signals.declined_now`, and in P1 a
    reply carrying no affirmation token sets `declined_now`. So:

        CallState(phase="p1_open") + "Meeting fix karo"
          -> signals: declined_now=True, asks_to_book=True
          -> stays in p1_open

    A lead who answers "kya abhi 2 minute baat ho sakti hai?" with "book me a meeting" is
    the readiest lead on the list, and the machine reads them as hanging up. Every other
    phase handles it (the test above). This is the user's own instruction — "activity
    detection should be powerful... it's not necessary to follow sequential paths" — with
    one phase still missing.

    Deliberately NOT fixed here: this is a test change, and `declined_now` gates the
    compliance path where a lead really is refusing the call. It needs its own change and
    its own live call.
    """
    state = _state(phase="p1_open")
    _advance(state, "Meeting fix karo")
    assert state.phase == P5_PIVOT, (
        "NOT FIXED: an explicit booking request in P1 is discarded as a declined call — "
        "declined_now vetoes asks_to_book in machine.py:88-93"
    )


def test_the_routing_decision_cannot_make_a_network_call():
    """Proven by signature, not by a spy nobody passed anything to.

    A predicate that takes ONLY `text` has no client to call with — that is a structural
    guarantee, where "I passed a mock and it stayed at zero" would just be asserting that
    an unused object went unused. If one of these ever grows a `client` parameter, the
    sub-10ms bypass premise is gone and this fails at the signature.
    """
    import inspect

    for fn in (lead_wants_out, wants_to_book, defers_the_call):
        params = list(inspect.signature(fn).parameters)
        assert params == ["text"], (
            f"{fn.__name__}{tuple(params)} takes more than the utterance — a router that "
            "can reach the network is not a deterministic bypass"
        )


def test_the_routing_decision_is_fast_enough_to_be_worth_bypassing_for():
    """The bypass only pays if deciding is orders of magnitude cheaper than generating.

    Budget is deliberately loose (10ms for all 30 evaluations, against a ~1.2s completion):
    this is a smoke alarm for someone adding a model call or an unbounded regex, not a
    benchmark, and a tight bound would flake on a loaded CI box.
    """
    import time

    started = time.perf_counter()
    for text in ON_TOPIC + BYPASS_ELIGIBLE + BOOKING_INTENT:
        lead_wants_out(text)
        wants_to_book(text)
        defers_the_call(text)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 10, (
        f"30 routing decisions took {elapsed_ms:.1f}ms; the LLM turn they replace is ~1200ms"
    )


# --- red: the short-circuit that would act on them ---------------------------------------


def _shortcircuit():
    """The unbuilt router. None until it exists."""
    try:
        from roma.controller import shortcircuit
    except ImportError:
        return None
    return shortcircuit


def _require_router():
    mod = _shortcircuit()
    assert mod is not None, (
        "NOT BUILT: roma/controller/shortcircuit.py — canned_reply(state, user_text) -> "
        "str | None, consulted by PhaseControllerProcessor BEFORE it forwards the context "
        "frame. A non-None result is spoken via the LLMFullResponseStart/Text/End trio that "
        "PickupGreeter already uses (opening.py:412-415), and the completion is skipped. "
        "Measured cost of not having it: 1.9s per guarded turn on call 932b6c88."
    )
    return mod


def test_an_exit_request_is_answered_without_asking_the_model():
    mod = _require_router()
    for text in BYPASS_ELIGIBLE:
        state = _state()
        state.lead_wants_out = True
        assert mod.canned_reply(state, text) is not None, (
            f"{text!r} still requires a completion to answer"
        )


def test_a_pending_readback_is_answered_without_asking_the_model():
    """`slot_status == "accepted"` means the machine already chose the words: read the slot
    back. `SAFE_READBACK_HOLD_LINE` is literally a format string over it."""
    mod = _require_router()
    state = _state()
    state.slot_status = "accepted"
    state.accepted_slot = "2026-08-04T11:00:00+05:30"
    reply = mod.canned_reply(state, "haan theek hai")
    assert reply is not None, "a pending readback still costs a completion"
    assert "11:00" in reply, "the readback must contain the slot it is reading back"


def test_an_on_topic_question_is_never_short_circuited():
    """The over-reach guard, and the reason this file is not simply 5/5.

    Every one of these has an answer that depends on what the lead said. A router that
    returns a constant for any of them has turned Roma into an IVR.
    """
    mod = _require_router()
    for text in ON_TOPIC:
        assert mod.canned_reply(_state(), text) is None, (
            f"{text!r} was short-circuited to a fixed line — this question needs the model"
        )


def test_a_booking_request_is_never_short_circuited():
    """The specific over-reach this file was reorganised to prevent."""
    mod = _require_router()
    for text in BOOKING_INTENT:
        state = _state()
        state.phase = P5_PIVOT
        assert mod.canned_reply(state, text) is None, (
            f"{text!r} was answered with a canned line — a booking request needs an offer "
            "built from this lead's constraints, not a form letter"
        )


# --- red: the guarded-topic questions, where the answer is ALREADY a constant -------------
#
# These three are the strongest short-circuit candidates in the whole call, and they are a
# different shape from `lead_wants_out`. The lexicon does not merely detect them — docs/04
# has already DECIDED the reply: `SUBSTITUTIONS[category]` is the exact sentence Roma is
# permitted to say about money, salary and certificates, and the pre-TTS filter will force
# it regardless of what the model produces.
#
# So the model is being paid ~1.2s and a completion to write a line that is then thrown
# away for a string constant. That is the pure case for the bypass: not a heuristic guess
# about intent, but a topic whose answer is fixed by policy before the turn starts.
#
# The risk is over-reach, and it is real. "Fees kitni hai?" must get the deflection, but
# "Course mein kya modules hain?" is a normal question that needs a real answer. Each test
# below pins BOTH directions.

GUARDED_TOPIC_ASKS = [
    ("Fees kitni hai course ki?", BlockCategory.FEE),
    ("Placement ka percentage kya hai aapka?", BlockCategory.PLACEMENT),
    ("Google ka certificate milta hai kya?", BlockCategory.CERT),
]

ANSWERABLE_NEIGHBOURS = [
    "Course mein kya modules hain?",
    "Placement support kaisa hota hai?",
    "Certificate kis naam se milta hai?",
]


def test_a_guarded_topic_question_is_answered_from_the_lexicon_not_the_model():
    """RED. The 1.2s that is currently spent writing a line that gets overwritten."""
    mod = _require_router()
    for text, category in GUARDED_TOPIC_ASKS:
        reply = mod.canned_reply(_state(), text)
        assert reply is not None, (
            f"{text!r} still costs a completion, even though docs/04 already fixes the "
            f"answer to SUBSTITUTIONS[{category.name}]"
        )
        assert reply == SUBSTITUTIONS[category], (
            f"the bypass answered {text!r} with its own wording instead of the one line "
            "the guardrail permits — two sources of truth for a compliance sentence"
        )


def test_the_bypassed_answer_is_the_same_one_the_filter_would_have_forced():
    """The bypass must be a SHORTCUT, not a second policy.

    If these ever diverge, a lead gets a different answer about money depending on whether
    the router fired — which is the worst possible place for a behavioural fork.
    """
    mod = _require_router()
    for text, _category in GUARDED_TOPIC_ASKS:
        reply = mod.canned_reply(_state(), text)
        if reply is None:
            continue
        # What the filter would do to a model answer that leaked a number on this topic.
        assert safe_output(reply) == reply, (
            f"the canned reply for {text!r} would itself be blocked by the filter"
        )
        assert screen(reply).allowed, f"{text!r} produced a line the guardrail rejects"


def test_an_answerable_question_on_the_same_topic_still_reaches_the_model():
    """The over-reach guard, and the reason this is three tests and not one.

    "Placement ka percentage kya hai" is fixed by policy. "Placement support kaisa hota
    hai" is a real question with a real answer that depends on this lead's background. A
    router that cannot tell them apart has turned the guardrail into a topic ban, and Roma
    stops being able to discuss the course she is selling.
    """
    mod = _require_router()
    for text in ANSWERABLE_NEIGHBOURS:
        assert mod.canned_reply(_state(), text) is None, (
            f"{text!r} was short-circuited — it is answerable, and canning it makes Roma "
            "refuse to talk about her own course"
        )


def test_the_canned_replies_come_from_confirmguard_not_a_second_copy():
    """One place owns Roma's fixed lines. A router with its own string table is how "Ek
    minute ji" came to have five separate sources earlier in this build."""
    mod = _require_router()
    state = _state()
    state.slot_status = "none"
    state.lead_wants_out = False
    reply = mod.canned_reply(state, "abhi time tay nahi hua")
    if reply is not None:
        assert reply in {SAFE_HOLD_LINE}, (
            "the router invented a line instead of reusing confirmguard's"
        )


def test_every_short_circuited_line_still_passes_the_guardrail():
    """docs/04: every branch that can emit audio keeps `safe_output` on it. Skipping the
    LLM must not skip the filter — the pre-TTS processor is downstream of where these
    frames are injected, so this holds by pipeline position, and this test pins it."""

    mod = _require_router()
    for text in BYPASS_ELIGIBLE:
        state = _state()
        state.lead_wants_out = True
        reply = mod.canned_reply(state, text)
        if reply is not None:
            assert safe_output(reply) == reply, (
                f"the canned reply for {text!r} would itself be substituted by the filter"
            )
