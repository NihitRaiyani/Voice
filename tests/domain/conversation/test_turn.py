"""Orchestrator integration (docs/03 + docs/06). Extraction is faked (no live API); the
classifier and machine are the real ones. Covers the full P1→P7 win path, the objection
loop + cap, the P2 turn-budget bail, and checkpoint-on-durable-events only.

Async driven with asyncio.run (repo convention)."""

import asyncio
from datetime import datetime

import pytest

from roma.controller.machine import P2_MAX_TURNS
from roma.controller.pacing import CLOSE_SECS, HURRY_SECS, OVER_SECS
from roma.controller.slots import DiscoveryValue, TimeSlot
from roma.controller.state import DISCOVERY_ORDER, CallState
from roma.controller.timeresolve import IST
from roma.controller.turn import advance_turn, is_affirmation

NOW = datetime(2026, 7, 25, 8, 0, tzinfo=IST)
CLIENT = object()


async def _fake_discovery(client, text, slot_name):
    return DiscoveryValue(value=text or "x", confidence=0.9)


async def _fake_discovery_lowconf(client, text, slot_name):
    return DiscoveryValue(value=text, confidence=0.2)


async def _fake_time(client, text, *, offered=None):
    if "accept" in text:
        return TimeSlot(accepted=True, day_offset=1, hour=5, period="evening", confidence=0.9)
    if "confirm" in text:
        return TimeSlot(readback_confirmed=True, confidence=0.9)
    return TimeSlot(confidence=0.0)


def _advance(state, text, **kw):
    kw.setdefault("client", CLIENT)
    kw.setdefault("now", NOW)
    kw.setdefault("extract_discovery", _fake_discovery)
    kw.setdefault("extract_time", _fake_time)
    return asyncio.run(advance_turn(state, text, **kw))


def test_full_p1_to_p7_win_path():
    s = CallState(call_sid="CA_win")

    assert _advance(s, "haan ji").next_phase == "p2_discover"
    # "Asha" first: the name is the opening discovery slot now that Roma is inbound.
    for answer in ["Asha", "12th", "2020", "student", "Vadodara"][: len(DISCOVERY_ORDER)]:
        _advance(s, answer)
    assert s.phase == "p3_value"
    assert s.filled_discovery_count() == len(DISCOVERY_ORDER)

    _advance(s, "achha")
    assert s.phase == "p3_value"
    _advance(s, "ye course kya hai")
    assert s.phase == "p3_value"
    _advance(s, "kitne mahine ka hai")
    assert s.phase == "p3_value"
    _advance(s, "iske baad kya kar sakte hain")
    assert s.phase == "p4_structure"
    _advance(s, "theek")
    assert s.phase == "p5_pivot"

    t = _advance(s, "haan Monday accept hai")
    assert s.phase == "p7_close" and s.accepted_slot is not None

    t = _advance(s, "haan confirm theek hai")
    assert t.win is True and s.locked_slot == s.accepted_slot


def test_p2_bails_to_p3_after_turn_budget_when_slots_never_fill():
    s = CallState(call_sid="CA_evasive", phase="p2_discover")
    for _ in range(P2_MAX_TURNS):
        _advance(s, "hmm", extract_discovery=_fake_discovery_lowconf)
    assert s.phase == "p2_discover", "the valve must not fire ON the budget turn"
    _advance(s, "hmm", extract_discovery=_fake_discovery_lowconf)
    assert s.phase == "p3_value" and s.filled_discovery_count() == 0


def test_objection_in_p5_routes_to_p6_then_back_to_p5():
    s = CallState(call_sid="CA_obj", phase="p5_pivot")
    assert _advance(s, "ye to bahut mehenga hai").next_phase == "p6_objection"
    assert s.phase == "p6_objection" and s.objection_counts["cost"] == 1
    assert _advance(s, "achha theek hai samajh gaya").next_phase == "p5_pivot"
    assert s.phase == "p5_pivot"


def test_same_objection_twice_hard_pivots_to_p5():
    s = CallState(call_sid="CA_hard", phase="p5_pivot")
    _advance(s, "mehenga hai")
    assert s.phase == "p6_objection"
    t = _advance(s, "phir bhi mehenga lagta hai")
    assert t.next_phase == "p5_pivot" and t.hard_pivot is True
    assert s.objection_counts["cost"] == 2


def test_checkpoint_written_on_transition_and_slot_fill_not_on_noop():
    class _CountingStore:
        def __init__(self):
            self.saves = 0

        async def load(self, call_sid):
            return None

        async def save(self, state):
            self.saves += 1

    store = _CountingStore()
    s = CallState(call_sid="CA_ckpt", phase="p5_pivot")

    _advance(s, "kitne log hote hain batch mein", store=store)
    assert store.saves == 1
    assert s.slots_offered

    _advance(s, "aur kitne log hote hain", store=store)
    assert store.saves == 1

    _advance(s, "mehenga hai", store=store)
    assert store.saves == 2


@pytest.mark.parametrize(
    "text",
    ["હા જી", "હા", "જી", "હા એ સહી છે", "haan ji", "हाँ जी", "theek hai"],
)
def test_affirmation_covers_gujarati_hindi_and_roman(text):
    assert is_affirmation(text) is True


def test_gujarati_affirmation_advances_p1_to_p2():
    s = CallState(call_sid="CA_gu", phase="p1_open")
    assert _advance(s, "હા જી").next_phase == "p2_discover"


def test_a_caller_who_asks_who_picked_up_stays_in_p1():
    """P1 is a BRANCH, not a script. Roma's opener is a cached "Hello, Weltec Institute"; a
    caller who replies "કોણ બોલો છો" ("who's speaking?") did not catch it, and the only
    correct reply is to say it again. Advancing them to P2 would answer "who are you?" with
    "what's your name?", which is the rudest turn in the call."""
    s = CallState(call_sid="CA_who", phase="p1_open")
    assert _advance(s, "કોણ બોલો છો").next_phase == "p1_open"


def test_naming_us_back_is_a_question_not_a_confirmation():
    """ "Weltec hai?" is someone checking they reached the right number."""
    from roma.controller.turn import asks_who_we_are

    assert asks_who_we_are("Weltec hai")
    assert asks_who_we_are("kaun bol raha hai")
    s = CallState(call_sid="CA_conf", phase="p1_open")
    assert _advance(s, "haan Weltec hai").next_phase == "p1_open", (
        "an affirmation wrapped around an identity question is still the question"
    )


def test_naming_us_inside_a_real_enquiry_is_not_an_identity_question():
    """The false positive that would strand every caller who mentions us by name: "Weltec ke
    digital marketing course ke baare mein poochhna tha" is business, not "who is this?"."""
    from roma.controller.turn import asks_who_we_are

    assert not asks_who_we_are("Weltec ke digital marketing course ke baare mein poochhna tha")
    s = CallState(call_sid="CA_biz", phase="p1_open")
    assert _advance(s, "Weltec ke course ke baare mein janna tha").next_phase == "p2_discover"


def test_discovery_slot_not_reasked_after_fill():
    s = CallState(call_sid="CA_slot", phase="p2_discover", lead_name="Asha")
    _advance(s, "job karta hoon")
    assert s.current_status == "job karta hoon"
    assert s.next_discovery_slot() == "education"


REFUSALS = [
    "જી ના",
    "जी नहीं",
    "ના જી",
    "ના",
    "नहीं",
    "ઠીક નથી",
    "બરાબર નથી",
    "nahi ji",
    "no thanks",
    "abhi nahi",
    "ના ભાઈ",
]

AFFIRMATIONS = [
    "હા જી",
    "haan ji",
    "જી",
    "હા",
    "हाँ",
    "ठीक है",
    "બરાબર",
    "ok",
    "theek hai",
    "bilkul",
    "ચોક્કસ",
    "haan haan",
]


@pytest.mark.parametrize("text", REFUSALS)
def test_a_refusal_is_never_an_affirmation(text):
    assert is_affirmation(text) is False, f"{text!r} would advance the call on a NO"


@pytest.mark.parametrize("text", AFFIRMATIONS)
def test_real_affirmations_still_pass(text):
    assert is_affirmation(text) is True


def test_tag_particle_na_is_not_a_refusal():
    """Bare romanized `na` is the agreement-seeking tag in "theek hai na?", not "no".
    It is deliberately absent from the negation set; the Indic `ના` is not."""
    assert is_affirmation("theek hai na") is True
    assert is_affirmation("haan na") is True
    assert is_affirmation("ના") is False


def test_p1_does_not_advance_on_a_polite_gujarati_refusal():
    """The end-to-end consequence, not just the predicate."""
    s = CallState(call_sid="CA_refuse", phase="p1_open")
    _advance(s, "જી ના")
    assert s.phase == "p1_open"


async def _six_am(client, text, *, offered=None):
    return TimeSlot(accepted=True, day_offset=1, hour=6, period="morning", confidence=0.9)


def test_a_six_am_request_does_not_accept_a_slot_and_says_why():
    s = CallState(call_sid="CA_6am", phase="p5_pivot")
    _advance(s, "kal subah chhe baje aa jaunga", extract_time=_six_am)

    assert s.accepted_slot is None
    assert s.phase == "p5_pivot"
    assert s.slot_status == "out_of_hours"


def test_the_refusal_is_visible_in_the_prompt_the_model_will_read():
    """The state field is only half the fix; the value of it is that it renders into the
    phase prompt. Assert on what the model actually sees."""
    from roma.llm.prompts import assemble_system_prompt

    s = CallState(call_sid="CA_6am", phase="p5_pivot")
    _advance(s, "kal subah chhe baje aa jaunga", extract_time=_six_am)

    prompt = assemble_system_prompt(s.as_prompt_vars(), s.phase)
    assert "confirm bilkul mat karo" in prompt


def test_an_accepted_slot_reaches_the_close_prompt_as_a_readable_day_and_time():
    from roma.llm.prompts import assemble_system_prompt

    s = CallState(call_sid="CA_ok", phase="p5_pivot")
    _advance(s, "haan Monday accept hai")

    assert s.slot_status == "accepted"
    assert "Sunday 26 July, 5:00 PM" in assemble_system_prompt(s.as_prompt_vars(), s.phase)


def test_locking_the_slot_sets_the_locked_status():
    s = CallState(call_sid="CA_lock", phase="p5_pivot")
    _advance(s, "haan Monday accept hai")
    _advance(s, "haan confirm theek hai")
    assert s.locked_slot is not None and s.slot_status == "locked"


async def _extractor_that_misses_the_confirm(client, text, *, offered=None):
    if "accept" in text:
        return TimeSlot(accepted=True, day_offset=1, hour=5, period="evening", confidence=0.9)
    return TimeSlot(confidence=0.0)


def _at_close(**kw):
    s = CallState(call_sid="CA_rb", phase="p5_pivot")
    _advance(s, "haan Monday accept hai", **kw)
    assert s.phase == "p7_close" and s.accepted_slot is not None
    return s


def test_a_plain_haan_locks_the_visit_when_the_extractor_misses_it():
    """THE regression."""
    kw = {"extract_time": _extractor_that_misses_the_confirm}
    s = _at_close(**kw)
    t = _advance(s, "haan ji theek hai", **kw)
    assert t.win is True
    assert s.locked_slot == s.accepted_slot


def test_the_gujarati_confirm_locks_too():
    """STT runs gu-IN (D3) — a romanized-only fallback would be blind on live calls."""
    kw = {"extract_time": _extractor_that_misses_the_confirm}
    s = _at_close(**kw)
    assert _advance(s, "હા બરાબર", **kw).win is True


def test_a_refusal_at_the_readback_does_NOT_lock():
    """`is_affirmation` vetoes on any negation, so "ઠીક નથી" ("not okay") must stay a
    refusal. A false lock here is a phantom booking, the worst outcome of the call."""
    kw = {"extract_time": _extractor_that_misses_the_confirm}
    s = _at_close(**kw)
    t = _advance(s, "ના ઠીક નથી", **kw)
    assert t.win is False and s.locked_slot is None


def test_the_fallback_cannot_invent_a_slot_that_was_never_accepted():
    """The lock still requires an `accepted_slot` the resolver already validated — the
    fallback loosens the CONFIRM detector, never the slot itself."""
    s = CallState(call_sid="CA_noslot", phase="p7_close")
    t = _advance(s, "haan bilkul confirm", extract_time=_extractor_that_misses_the_confirm)
    assert t.win is False and s.locked_slot is None


def test_the_extractor_still_wins_when_it_does_report_a_confirm():
    """The fallback is an OR, not a replacement — the model-backed path is unchanged."""
    s = CallState(call_sid="CA_both", phase="p5_pivot")
    _advance(s, "haan Monday accept hai")
    assert _advance(s, "confirm").win is True


def test_the_clock_is_recorded_on_state_so_the_prompt_can_read_it():
    s = CallState(call_sid="CA_clock", phase="p2_discover")
    _advance(s, "BCom", elapsed_secs=42.0)
    assert s.elapsed_secs == 42.0
    assert "TIME:" not in s.as_prompt_vars()["pacing"]


def test_discovery_is_cut_short_once_the_call_runs_long():
    """Five unfilled slots would normally keep the machine in P2. Past the hurry band the
    booking is worth more than the sixth question."""
    s = CallState(call_sid="CA_hurry", phase="p2_discover")
    t = _advance(s, "hmm", extract_discovery=_fake_discovery_lowconf, elapsed_secs=HURRY_SECS)
    assert t.next_phase == "p5_pivot" and t.hard_pivot is True
    assert s.phase == "p5_pivot"


def test_the_same_turn_stays_in_discovery_when_there_is_time():
    """The mirror of the test above, differing ONLY in the clock — otherwise the assertion
    above could pass for the wrong reason."""
    s = CallState(call_sid="CA_calm", phase="p2_discover")
    t = _advance(s, "hmm", extract_discovery=_fake_discovery_lowconf, elapsed_secs=10.0)
    assert t.next_phase == "p2_discover" and t.hard_pivot is False


def test_the_value_pitch_is_skipped_when_the_clock_has_run_out():
    s = CallState(call_sid="CA_skip", phase="p2_discover")
    for slot in ("education", "passing_year", "current_status", "city", "timing_constraint"):
        setattr(s, slot, "x")
    t = _advance(s, "haan", elapsed_secs=CLOSE_SECS)
    assert t.next_phase == "p5_pivot"


def test_an_objection_still_routes_to_p6_however_late_it_is():
    """A lead who raises a real objection at five minutes gets an answer. Cutting them off
    to re-offer slots is evasion, and it is the one thing docs/03 says loses a warm lead.

    Started from P5 because that is where the objection classifier actually runs —
    `advance_turn` only classifies in P5/P6/P7, so an objection raised during discovery is
    not detected at all. That predates the budget and is untouched by it."""
    s = CallState(call_sid="CA_late_obj", phase="p5_pivot")
    t = _advance(s, "ye to bahut mehenga hai", elapsed_secs=OVER_SECS)
    assert t.next_phase == "p6_objection"
    assert s.objection_counts["cost"] == 1


def test_a_forced_pivot_does_not_bounce_the_call_out_of_p6():
    """The turn AFTER the forced pivot must not immediately re-force. P6 is excluded, so a
    lead still being answered stays answered."""
    s = CallState(call_sid="CA_p6_stay", phase="p6_objection")
    t = _advance(s, "achha theek hai samajh gayi", elapsed_secs=OVER_SECS)
    assert t.next_phase == "p5_pivot"
    assert t.hard_pivot is False


def test_the_close_is_never_dragged_backwards_by_the_clock():
    """P7 with an accepted slot is one confirm away from the win. A forced pivot here would
    throw the booking away at the last moment."""
    s = CallState(call_sid="CA_late_close", phase="p5_pivot")
    _advance(s, "haan Monday accept hai", elapsed_secs=10.0)
    assert s.phase == "p7_close"
    t = _advance(s, "haan confirm theek hai", elapsed_secs=OVER_SECS)
    assert t.next_phase == "p7_close" and t.win is True


def test_the_default_is_a_call_that_has_just_started():
    """Every pre-existing caller omits `elapsed_secs`; none of them may start pivoting."""
    s = CallState(call_sid="CA_default", phase="p2_discover")
    _advance(s, "hmm", extract_discovery=_fake_discovery_lowconf)
    assert s.phase == "p2_discover" and s.elapsed_secs == 0.0


@pytest.mark.parametrize("phase", ["p1_open", "p3_value", "p4_structure"])
def test_an_objection_routes_to_p6_from_any_phase(phase):
    s = CallState(call_sid=f"CA_obj_{phase}", phase=phase)
    assert _advance(s, "ye to bahut mehenga hai").next_phase == "p6_objection"
    assert s.objection_counts["cost"] == 1


def test_an_answered_discovery_question_is_an_answer_not_an_objection():
    """The false positive that hoisting exposed. Roma asks "padh rahe ho ya job kar rahe
    ho?" and the lead says "job dhoondh raha hoon" — a perfectly good answer that the
    topical objection lexicon reads as a placement objection. In P2 a reply that FILLED the
    slot Roma asked for is an answer, whatever words it used."""
    s = CallState(
        call_sid="CA_p2_answer",
        phase="p2_discover",
        lead_name="Asha",
        education="BCom",
        passing_year="2022",
    )
    t = _advance(s, "job dhoondh raha hoon")
    assert t.next_phase == "p2_discover", "a discovery answer derailed the call into P6"
    assert s.current_status == "job dhoondh raha hoon"
    assert s.objection_counts == {}


def test_a_p2_reply_that_answers_nothing_can_still_be_an_objection():
    """The other half — without this the exception above would simply disable P2."""
    s = CallState(call_sid="CA_p2_obj", phase="p2_discover")
    t = _advance(s, "pehle fees batao kitni hai", extract_discovery=_fake_discovery_lowconf)
    assert t.next_phase == "p6_objection"
    assert s.objection_counts


def test_an_objection_and_an_acceptance_in_one_breath_record_both():
    """ "Tuesday time nathi, Monday chalega" tripped the objection lexicon, and the old
    `if not objection_recorded` short-circuit meant the Monday acceptance was never even
    parsed. The machine still routes on the objection; the slot is waiting when P6 returns."""
    s = CallState(call_sid="CA_both", phase="p5_pivot")
    t = _advance(s, "abhi time nahi hai lekin accept kar leta hoon")
    assert t.next_phase == "p6_objection"
    assert s.objection_counts
    assert s.accepted_slot is not None, "the acceptance in the same breath was dropped"


TUE_11 = "2026-07-28T11:00:00+05:30"
TUE_17 = "2026-07-28T17:00:00+05:30"


async def _picks_offer_two(client, text, *, offered=None):
    """What the extractor now returns for "I'll come Tuesday" when Tuesday was offered."""
    return TimeSlot(accepted=True, chose_offer=2, confidence=0.9)


async def _day_only(client, text, *, offered=None):
    """A day named with no hour — the shape that used to collapse to `unclear`."""
    return TimeSlot(accepted=True, weekday="tuesday", confidence=0.9)


def _at_pivot_with_offers(**kw):
    s = CallState(call_sid="CA_offer", phase="p5_pivot")
    _advance(s, "kaunsa slot", **kw)
    return s


def test_picking_an_offered_slot_books_it():
    """THE regression."""
    s = _at_pivot_with_offers()
    offered = list(s.slots_offered)
    t = _advance(s, "મેં Tuesday કુ આઉંગા", extract_time=_picks_offer_two)
    assert s.accepted_slot == offered[1], "the lead's choice was not recorded"
    assert s.slot_status == "accepted"
    assert t.next_phase == "p7_close"


def test_a_day_with_no_time_keeps_the_day_instead_of_starting_over():
    """`unclear` told Roma to re-ask from scratch, throwing away a day the lead had named
    and making her sound like she was not listening."""
    s = CallState(call_sid="CA_dayonly", phase="p5_pivot")
    s.slots_offered = [TUE_11, TUE_17]
    _advance(s, "Tuesday", extract_time=_day_only)
    assert s.slot_status == "day_only"
    assert s.accepted_slot is None


def test_a_day_with_only_one_offer_on_it_is_not_ambiguous():
    """ "Tuesday" when only one Tuesday slot was offered IS a choice."""
    s = CallState(call_sid="CA_one", phase="p5_pivot")
    s.slots_offered = [TUE_17]
    _advance(s, "Tuesday", extract_time=_day_only)
    assert s.accepted_slot == TUE_17
    assert s.slot_status == "accepted"


def test_a_non_acceptance_writes_a_status_instead_of_vanishing():
    """The silence itself was the bug: no state, no log, no checkpoint, nothing to debug."""
    s = _at_pivot_with_offers()
    s.slot_status = "out_of_hours"
    _advance(s, "hmm pata nahi")
    assert s.slot_status == "none", "a stale refusal survived the turn"


def test_every_p5_turn_logs_a_verdict(caplog):
    """The absence of this line on a slot-bearing turn must be impossible — it is what
    makes the failure greppable next time."""
    import logging

    s = _at_pivot_with_offers()
    with caplog.at_level(logging.INFO, logger="roma.controller"):
        _advance(s, "hmm pata nahi")
    assert any("slot verdict:" in r.message for r in caplog.records)


def test_the_lead_s_words_never_reach_an_info_log(caplog):
    """A transcript is lead PII (docs/07:15): decision signatures at INFO, text at DEBUG."""
    import logging

    s = _at_pivot_with_offers()
    with caplog.at_level(logging.INFO, logger="roma.controller"):
        _advance(s, "mera naam Nihit hai aur main Vadodara se hoon")
    for r in caplog.records:
        assert "Nihit" not in r.getMessage()


async def _revises_to_offer_one(client, text, *, offered=None):
    return TimeSlot(accepted=True, chose_offer=1, confidence=0.9)


def test_a_new_time_at_the_readback_revises_rather_than_locking():
    """Neither arm used to match this: the elif required a confirm, so `accepted_slot` was
    never updated and `slot_status` still read "accepted" for the time the lead just
    rejected."""
    s = _at_pivot_with_offers()
    offered = list(s.slots_offered)
    _advance(s, "doosra wala", extract_time=_picks_offer_two)
    assert s.phase == "p7_close" and s.accepted_slot == offered[1]

    t = _advance(s, "haan pehla wala better rahega", extract_time=_revises_to_offer_one)
    assert s.accepted_slot == offered[0], "the revision was ignored"
    assert s.locked_slot is None, "a revision must not lock"
    assert t.win is False


def test_a_plain_confirm_still_locks():
    """The other half — the revision check must not swallow an ordinary confirmation."""
    s = _at_pivot_with_offers()
    _advance(s, "doosra wala", extract_time=_picks_offer_two)
    t = _advance(s, "haan confirm theek hai")
    assert t.win is True and s.locked_slot == s.accepted_slot


def test_a_mishear_at_the_readback_does_not_clobber_the_accepted_slot():
    """An "ek minute" must not flip `slot_status` to unclear — that tells the p7 prompt she
    has nothing to read back and sends her round to re-offer a slot already accepted."""
    s = _at_pivot_with_offers()
    _advance(s, "doosra wala", extract_time=_picks_offer_two)
    accepted = s.accepted_slot
    _advance(s, "ek minute rukiye")
    assert s.accepted_slot == accepted
    assert s.slot_status == "accepted"


async def _claims_offer_one(client, text, *, offered=None):
    """The extractor as it actually behaved on the live call: `accepted` and `chose_offer`
    at full confidence, on a reply that was a flat refusal."""
    return TimeSlot(accepted=True, chose_offer=1, confidence=1.0)


def test_a_flat_refusal_cannot_book_a_slot():
    """THE regression. Roma had offered two slots; the lead said `નહીં નહીં કોઈ દૂસરા item
    ના દો` — "no no, don't give me another one" — and the extractor returned
    accepted=True chose_offer=1 confidence=1.00. The machine booked it and Roma read the
    booking back to a lead who was refusing."""
    s = _at_pivot_with_offers()
    t = _advance(s, "નહીં નહીં કોઈ દૂસરા item ના દો", extract_time=_claims_offer_one)
    assert s.accepted_slot is None, "a refusal was recorded as a booking"
    assert s.slot_status == "none"
    assert t.next_phase != "p7_close", "a refusal must not reach the readback"


def test_a_refusal_carrying_a_time_is_still_an_acceptance():
    """The other half. `નહીં મેં 3 બજે આઉંગા` — "no, I'll come at 3" — is a counter-offer,
    and the veto must not eat it. The digit is the evidence."""
    s = _at_pivot_with_offers()
    _advance(s, "નહીં મેં 3 બજે આઉંગા", extract_time=_claims_offer_one)
    assert s.accepted_slot is not None, "a counter-offer was vetoed as a refusal"


def test_a_refusal_scoped_by_a_contrast_marker_is_not_a_refusal():
    """ "...nahi hai LEKIN accept kar leta hoon" — the negation belongs to the first clause.
    Without the contrast marker the veto swallowed a genuine reluctant acceptance."""
    s = _at_pivot_with_offers()
    _advance(s, "aaj time nahi hai lekin theek hai", extract_time=_claims_offer_one)
    assert s.accepted_slot is not None


def test_the_veto_discards_the_whole_verdict_not_just_the_claim():
    """The TimeSlot came from one model call on one utterance. A claim the reply cannot
    support discredits the day it invented too — otherwise a hallucinated weekday silently
    becomes the day under discussion and anchors every later hour to it."""
    s = _at_pivot_with_offers()
    before = s.pending_day
    _advance(s, "નહીં નહીં", extract_time=_claims_offer_one)
    assert s.pending_day == before, "a vetoed verdict moved the day under discussion"


async def _hour_only_three_pm(client, text, *, offered=None):
    """An hour and NO day — what the extractor should return for "main teen baje aaunga"."""
    return TimeSlot(accepted=True, hour=3, confidence=0.9)


def test_an_hour_with_no_day_lands_on_the_day_under_discussion():
    """THE regression. Roma had moved the visit to Wednesday; the lead said `નહીં મેં 3 બજે
    આઉંગા` and the machine booked MONDAY 15:00 — the stale day off the offer list — then
    told them the branch was closed then. The day the conversation is on is now remembered."""
    s = CallState(call_sid="CA_anchor", phase="p5_pivot")
    _advance(s, "kaunsa slot")
    s.pending_day = "2026-07-29"
    _advance(s, "main teen baje aaunga", extract_time=_hour_only_three_pm)
    assert s.accepted_slot is not None
    assert s.accepted_slot.startswith("2026-07-29T15:00"), s.accepted_slot


def test_an_hour_with_no_day_and_no_anchor_stays_unresolved():
    """The anchor is memory, not a guess.

    Rewritten when the offers began seeding the anchor (CA4777470): "no anchor" can no
    longer be produced by simply arriving at P5, because arriving at P5 puts a day on the
    table. It IS produced when the two offers fall on DIFFERENT days — Roma proposes
    "today five, or tomorrow eleven" and the lead says "teen baje". That is genuinely
    ambiguous and Roma must ask which day rather than pick one.
    """
    midday = datetime(2026, 7, 25, 12, 0, tzinfo=IST)
    s = CallState(call_sid="CA_noanchor", phase="p5_pivot")
    _advance(s, "kaunsa slot", now=midday)
    assert len({iso[:10] for iso in s.slots_offered}) == 2, s.slots_offered
    assert s.pending_day is None, "an ambiguous offer pair must not anchor anything"

    _advance(s, "main teen baje aaunga", now=midday, extract_time=_hour_only_three_pm)
    assert s.accepted_slot is None


def test_naming_a_day_records_it_as_the_day_under_discussion():
    s = CallState(call_sid="CA_pending", phase="p5_pivot")
    s.slots_offered = [TUE_11, TUE_17]
    _advance(s, "Tuesday", extract_time=_day_only)
    assert s.pending_day == "2026-07-28"


def test_accepting_a_time_re_pins_the_offers_to_that_day():
    """The offers Roma speaks are also the offers the extractor is shown. A list left on
    the old day is what taught the model to answer "Wednesday" with Monday."""
    s = CallState(call_sid="CA_repin", phase="p5_pivot")
    _advance(s, "kaunsa slot")
    s.pending_day = "2026-07-29"
    _advance(s, "main teen baje aaunga", extract_time=_hour_only_three_pm)
    assert s.slots_offered, "the offer list was emptied"
    assert all(iso.startswith("2026-07-29") for iso in s.slots_offered), s.slots_offered


async def _bare_four_lowconf(client, text, *, offered=None):
    """The extractor as it actually behaved: `hour` extracted correctly, then hedged at
    0.50 — under CONFIDENCE_THRESHOLD, so the whole slot was thrown away."""
    return TimeSlot(accepted=True, hour=4, confidence=0.50)


def test_a_bare_hour_against_the_offers_books_on_the_offered_day():
    """THE regression. The lead said `4 બજે` three times and every turn logged
    `reason=unclear ... hour=4 anchor=- resolved=-`. Two causes, both here: nothing had
    seeded the anchor (they answered the offer rather than naming a day), and the model's
    own 0.50 sank a slot it had extracted correctly."""
    s = _at_pivot_with_offers()
    offer_day = s.slots_offered[0][:10]
    _advance(s, "4 બજે", extract_time=_bare_four_lowconf)
    assert s.accepted_slot is not None, "a plainly stated time resolved to nothing"
    assert s.accepted_slot.startswith(f"{offer_day}T16:00"), s.accepted_slot


def test_speaking_the_offers_is_what_puts_the_day_on_the_table():
    """Roma proposing two times on one day settles the day between them — which is why the
    lead then answers with a bare hour."""
    s = _at_pivot_with_offers()
    assert s.pending_day == s.slots_offered[0][:10]


def test_offers_on_two_different_days_anchor_nothing():
    """Unanimity is not a formality. `StaticHoursCalendar` walks forward from `now`, so
    past 11:00 it offers TODAY 17:00 and TOMORROW 11:00 — and "4 baje" against those is
    genuinely ambiguous. A coin toss on the lead's appointment is worse than a re-ask."""
    midday = datetime(2026, 7, 25, 12, 0, tzinfo=IST)
    s = CallState(call_sid="CA_split", phase="p5_pivot")
    _advance(s, "kaunsa slot", now=midday)
    assert s.pending_day is None


async def _no_structure_lowconf(client, text, *, offered=None):
    return TimeSlot(confidence=0.10)


def test_the_floor_needs_structure_as_well_as_words():
    """Both halves are required, and neither is the model's opinion of itself. With no
    hour, weekday, day_offset or chose_offer there is nothing to corroborate — flooring
    would be inventing a slot, which is precisely what the threshold exists to stop."""
    s = _at_pivot_with_offers()
    _advance(s, "4 બજે", extract_time=_no_structure_lowconf)
    assert s.accepted_slot is None


def test_the_floor_needs_words_as_well_as_structure():
    """The other half: a model that returns hour=4 on a reply with no time in it is
    hallucinating, and the transcript is what says so."""
    s = _at_pivot_with_offers()
    _advance(s, "achha samajh gaya", extract_time=_bare_four_lowconf)
    assert s.accepted_slot is None


def test_a_flat_refusal_is_never_floored_into_a_booking():
    """The floor must not re-open CA9933275. Belt AND braces: the refusal carries no time
    evidence so it is never floored, and `_is_refusal_only` vetoes the claim regardless."""
    s = _at_pivot_with_offers()
    _advance(s, "નહીં નહીં કોઈ દૂસરા ના દો", extract_time=_bare_four_lowconf)
    assert s.accepted_slot is None


def test_the_floor_is_logged(caplog):
    """A confidence override nobody can see in a log is how a threshold quietly stops
    meaning anything."""
    import logging

    s = _at_pivot_with_offers()
    with caplog.at_level(logging.INFO, logger="roma.controller"):
        _advance(s, "4 બજે", extract_time=_bare_four_lowconf)
    assert any("confidence floored" in r.getMessage() for r in caplog.records)


async def _three_pm_not_flagged_accepted(client, text, *, offered=None):
    """The extractor as it behaved on that call: the hour parsed, `accepted` stayed False
    because "3 baje hona" is not phrased as agreement to one of the two offers."""
    return TimeSlot(accepted=False, hour=3, confidence=0.9)


def test_naming_a_bookable_time_books_it_even_when_not_flagged_accepted():
    """THE regression. `3 बजे होना` resolved perfectly —

        slot verdict: reason=ok accepted=False hour=3 resolved=...T15:00 status=none

    — and was thrown away for `accepted=False`. `status=none` then told Roma nothing had
    been proposed while OFFER still named 11 and 5, and she said "three PM ka slot
    available nahi hai". Three PM is inside branch hours and the resolver had just said so.
    """
    s = _at_pivot_with_offers()
    _advance(s, "3 baje hona", extract_time=_three_pm_not_flagged_accepted)
    assert s.accepted_slot is not None, "a resolved, in-hours, future slot was discarded"
    assert s.accepted_slot.endswith("T15:00:00+05:30"), s.accepted_slot
    assert s.slot_status == "accepted"


async def _six_pm(client, text, *, offered=None):
    return TimeSlot(accepted=False, hour=6, period="evening", confidence=0.9)


def test_an_unresolvable_time_is_still_not_a_claim():
    """The inference needs a verdict that RESOLVED. `6 baje` is closing time — refused —
    and must stay refused rather than inferred into a booking."""
    s = _at_pivot_with_offers()
    _advance(s, "6 baje", extract_time=_six_pm)
    assert s.accepted_slot is None


def test_a_refused_time_tells_roma_why_it_was_refused():
    """The out_of_hours / in_past / day_only lines exist so Roma can EXPLAIN the refusal,
    and they only ever rendered when the model had labelled the reply an acceptance — which
    is precisely what it does NOT do for a time Roma has to decline.

    On CA0460d52 the lead said `6 बजे में था`; the resolver said out_of_hours and
    `slot_status` was written "none", so Roma was never told. She answered "main samajh
    nahi paayi" and named five PM, leaving the lead with no idea why six was impossible."""
    s = _at_pivot_with_offers()
    _advance(s, "6 baje", extract_time=_six_pm)
    assert s.slot_status == "out_of_hours"
    assert "band" in s.as_prompt_vars()["slot_status"], "the refusal is not explained"


def test_a_reply_with_no_time_in_it_is_never_inferred_into_a_claim():
    """Both halves are required. A resolution built from hallucinated fields must not be
    promoted just because it happens to parse."""
    s = _at_pivot_with_offers()
    _advance(s, "achha theek hai samajh gaya", extract_time=_three_pm_not_flagged_accepted)
    assert s.accepted_slot is None


def test_a_refusal_still_beats_an_inferred_claim():
    """Ordering: the inference runs first, the veto second, and the veto wins."""
    s = _at_pivot_with_offers()
    _advance(s, "નહીં નહીં કોઈ દૂસરા ના દો", extract_time=_three_pm_not_flagged_accepted)
    assert s.accepted_slot is None


# --- inbound P1 exit (docs/decisions.md, LOCKED 2026-07-31) -------------------------------


def test_a_caller_who_states_their_business_leaves_p1():
    """The bug the inbound eval fixtures caught. P1 used to advance only on an affirmation,
    because outbound asked a real question ("kya abhi 2 minute baat kar sakte hain?") and
    needed a real yes. An inbound caller says why they rang — "course ki information chahiye
    thi" — which carries no affirmation cue at all, so the call sat in P1 for its whole
    length while Roma re-greeted a person who had already explained themselves."""
    from roma.controller.turn import opened_the_conversation

    for opener in (
        "course ki information chahiye thi",
        "digital marketing ke baare mein poochhna tha",
        "મારે કોર્સ વિશે જાણવું હતું",
    ):
        assert opened_the_conversation(opener), opener


def test_an_affirmation_still_opens_the_conversation():
    """The outbound path is not broken by the inbound one — the seven pre-inbound fixtures
    all open with 'haan ji'."""
    from roma.controller.turn import opened_the_conversation

    assert opened_the_conversation("haan ji, maine hi inquiry kiya tha")


def test_a_wrong_number_does_not_get_walked_into_discovery():
    """A short bare negation is someone who dialled the wrong place, not an enquiry. Advancing
    them to P2 would have Roma asking a stranger's name for no reason."""
    from roma.controller.turn import opened_the_conversation

    for wrong in ("nahi", "nahi ji", "ના જી", "जी नहीं"):
        assert not opened_the_conversation(wrong), wrong
    assert not opened_the_conversation("")
    # Limitation, stated rather than hidden: the guard is the negation lexicon, so a wrong
    # number phrased without one ("galat number") does advance and Roma asks a name before
    # they hang up. Cheap to live with; not worth widening the lexicon the guardrail shares.
    assert opened_the_conversation("galat number")


def test_a_negation_inside_a_real_sentence_still_opens():
    """ "nahi, mujhe course ke baare mein poochhna tha" is an enquiry that happens to start
    with a no. Only the SHORT bare negation is a wrong number."""
    from roma.controller.turn import opened_the_conversation

    assert opened_the_conversation("nahi, mujhe course ke baare mein poochhna tha")


def test_the_name_is_captured_as_the_first_discovery_slot():
    s = CallState(call_sid="CA_name", phase="p2_discover")
    _advance(s, "Nikhil")
    assert s.lead_name == "Nikhil"
    assert s.next_discovery_slot() == "current_status"


def test_a_refused_name_does_not_swallow_the_slots_behind_it():
    """Without the attempt cap the pointer stayed on lead_name and every later answer was
    extracted against it, so P2 finished with an entirely empty profile."""
    from roma.controller.state import SLOT_ATTEMPT_CAP

    s = CallState(call_sid="CA_no_name", phase="p2_discover")
    for _ in range(SLOT_ATTEMPT_CAP):
        _advance(s, "naam rehne dijiye", extract_discovery=_fake_discovery_lowconf)
    assert s.lead_name is None, "a declined name must not be invented"
    assert s.next_discovery_slot() == "current_status"


def test_advance_turn_flags_a_dismissal_in_any_phase_and_never_clears_it():
    """On 049f0dc1 the lead asked the call to stop in P3, not P1, so a P1-only check (the
    shape `declined_now` already had) would never have seen it. Once set it must survive
    later turns that say nothing about stopping — otherwise the sign-off guard resumes
    pushing on the next turn."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    s = CallState(branch="Vadodara")
    s.phase = "p3_value"
    asyncio.run(advance_turn(s, "आप चले जाओ यहाँ से", client=None, now=NOW))
    assert s.lead_wants_out is True
    asyncio.run(advance_turn(s, "achha", client=None, now=NOW))
    assert s.lead_wants_out is True, "the flag was cleared by a later, neutral turn"


def test_a_course_question_moves_p2_to_p3_immediately():
    """Live call 049f0dc1 (2026-08-01). The lead asked five separate times, in P2, what the
    course was. P2 only exits when five slots fill or it times out after five turns, and the
    lead was complaining rather than answering — so neither happened for SIX turns and Roma
    answered from the P2 prompt, which forbids pitching and carries no course content. She
    improvised the same three facts out of the persona's phrase list, over and over.

    The lead asking what the course is IS the cue to go explain it."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    s = CallState(branch="Vadodara")
    s.phase = "p2_discover"
    asyncio.run(advance_turn(s, "मुझे course के बारे में बताइए", client=None, now=NOW))
    assert s.phase == "p3_value", "the course question did not move the machine to VALUE"


def test_answering_the_status_question_with_the_word_course_does_not_skip_discovery():
    """The precision half. P2's own status question is "padh rahe hain, koi course kar rahe
    hain, ya job kar rahe hain?" — so "course" lands in the ANSWER constantly. Advancing on
    that would skip discovery for a lead who never asked anything."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    s = CallState(branch="Vadodara")
    s.phase = "p2_discover"
    asyncio.run(advance_turn(s, "haan main koi course kar raha hoon", client=None, now=NOW))
    assert s.phase == "p2_discover"


def test_roma_is_told_what_day_it_is_from_the_same_clock_the_resolver_uses():
    """Live call e3237622 (2026-08-01, a SATURDAY). The lead offered a real, bookable slot —
    "main Wednesday ko aata hoon 2 baje" — and Roma refused it with "Aaj Wednesday hai, toh
    aap ab nahi aa sakte." She had no way to know the day, so she invented one, and a lead
    naming a day and a time turned into no booking (hard rule 4, NO INVENTION).

    Sourced from `now`, the same clock `resolve_visit_slot` uses, so the prompt's "today" and
    the machine's "today" cannot drift apart."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    s = CallState(branch="Vadodara")
    asyncio.run(advance_turn(s, "haan ji", client=None, now=NOW))
    assert s.today_iso == NOW.date().isoformat()
    today = s.as_prompt_vars()["today"]
    assert NOW.strftime("%A") in today, "the real weekday is not in the prompt"
    assert "aaj" in today.lower()


def test_an_unknown_date_renders_empty_rather_than_a_guess():
    """A missing day is survivable; a wrong one refuses real bookings."""
    from roma.controller.state import CallState

    assert CallState(branch="Vadodara").as_prompt_vars()["today"] == ""
    s = CallState(branch="Vadodara")
    s.today_iso = "not-a-date"
    assert s.as_prompt_vars()["today"] == ""


def test_the_lead_raising_a_time_is_detected_from_their_side():
    from roma.controller.turn import raises_the_visit_time

    for line in (
        "toh main kab aa sakta hoon?",
        "subah ka time milega?",
        "Thursday shaam ko aa jaunga",
        "कब आना है?",
        "morning slot theek rahega",
    ):
        assert raises_the_visit_time(line), line

    for line in ("mujhe course ke baare mein bataiye", "B.Com kar raha hoon", ""):
        assert not raises_the_visit_time(line), line


def test_insistence_is_counted_consecutively_and_resets():
    """Two IN A ROW, not two in the call. A lead who mentions a time, talks about fees for
    three turns, then mentions a time again is not pushing to book."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    state = CallState(phase="p3_value")

    asyncio.run(advance_turn(state, "kab aa sakta hoon?", client=None, now=NOW))
    assert state.lead_time_asks == 1

    asyncio.run(advance_turn(state, "placement kaisa hai?", client=None, now=NOW))
    assert state.lead_time_asks == 0, "a non-time turn must reset the run"


def test_two_consecutive_time_turns_pivot_out_of_p3():
    """The end-to-end path: detector -> counter -> signal -> transition."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    state = CallState(phase="p3_value")
    asyncio.run(advance_turn(state, "toh main kab aa sakta hoon?", client=None, now=NOW))
    asyncio.run(advance_turn(state, "subah ka time milega?", client=None, now=NOW))
    assert state.lead_time_asks >= 2
    assert state.phase == "p5_pivot", "four turns of course questions instead of an offer"


def test_asking_to_book_in_words_is_detected_without_any_clock_word():
    """THE gap behind ca529641. TIME_CUES holds only times of day, so a lead asking to
    schedule in plain words matched nothing and the pivot never fired."""
    from roma.controller.turn import raises_the_visit_time, wants_to_book

    for line in (
        "mujhe visit schedule karni hai",
        "meeting fix kar dijiye",
        "main aana chahta hoon",
        "appointment book kar do",
        "मुझे विजिट शेड्यूल करनी है",
        "kab milna ho sakta hai",
    ):
        assert wants_to_book(line), line

    # The point of the new detector: these carry NO clock word at all.
    assert not raises_the_visit_time("mujhe visit schedule karni hai")
    assert not raises_the_visit_time("meeting fix kar dijiye")

    for line in ("course ke baare mein bataiye", "B.Com kar raha hoon", ""):
        assert not wants_to_book(line), line


def test_one_explicit_ask_is_enough_no_second_turn_needed():
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    state = CallState(phase="p3_value")
    asyncio.run(advance_turn(state, "mujhe visit schedule karni hai", client=None, now=NOW))
    assert state.phase == "p5_pivot", "made a ready lead ask twice"


def test_a_lead_saying_goodbye_is_not_offered_a_slot():
    """ "Baad mein milte hain" carries a booking word and is a deferral. Answering it with
    a slot is exactly the push hard rule 6 forbids."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn

    state = CallState(phase="p3_value")
    asyncio.run(advance_turn(state, "baad mein milte hain", client=None, now=NOW))
    assert state.phase != "p5_pivot", "pushed a booking at a lead who was leaving"

    s2 = CallState(phase="p3_value")
    s2.lead_wants_out = True
    asyncio.run(advance_turn(s2, "visit schedule karo", client=None, now=NOW))
    assert s2.phase != "p5_pivot", "lead_wants_out must veto the pivot"


def test_the_exact_line_that_was_refused_on_call_56504a23():
    """Verbatim from the transcript. It contains "meeting", which is ALSO a standalone
    deferral cue ("abhi meeting mein hoon"), so `defers_the_call` fired on the very word
    carrying the booking intent and vetoed the pivot. Roma stayed in P3 and answered with
    another course suggestion."""
    import asyncio

    from roma.controller.state import CallState
    from roma.controller.turn import advance_turn, defers_the_call, wants_to_book

    line = (
        "मैंने आपके course के बारे में देखा था और मुझे आपके course के बारे में "
        "सब कुछ पता है तो अब हमारी meeting schedule fix करो"
    )
    assert wants_to_book(line)
    assert defers_the_call(line), "the collision is real; the fix is precedence, not removal"

    state = CallState(phase="p3_value")
    asyncio.run(advance_turn(state, line, client=None, now=NOW))
    assert state.phase == "p5_pivot", "an explicit ask to book was read as a deferral"


def test_meeting_alone_is_still_a_deferral_not_a_booking():
    """The whole reason "meeting" was ambiguous. Without a booking verb it means busy."""
    from roma.controller.turn import wants_to_book

    assert not wants_to_book("abhi meeting mein hoon")
    assert not wants_to_book("मैं अभी मीटिंग में हूँ")
    assert wants_to_book("meeting fix karo")
    assert wants_to_book("meeting schedule kar dijiye")


def test_unambiguous_booking_words_need_no_verb():
    from roma.controller.turn import wants_to_book

    assert wants_to_book("visit karni hai mujhe")
    assert wants_to_book("appointment chahiye")
    assert wants_to_book("schedule bata dijiye")


def test_an_unresolvable_revision_at_the_readback_never_locks_the_old_slot():
    """Call fa3ae274 — the worst outcome docs/03 names, reached with won=True.

    Roma read back Tuesday 11:00. The lead answered "मैंने बोला Wednesday को छह बजे का चार
    बजे का slot". Wednesday 18:00 is outside the 10-18 window, so the verdict was
    out_of_hours with slot=None; the revision arm needs a RESOLVED slot and skipped. The
    same sentence contains "ठीक है", `is_affirmation` fired, and Tuesday got locked."""
    from types import SimpleNamespace

    from roma.controller.state import CallState
    from roma.controller.turn import _close_turn

    state = CallState(phase="p7_close")
    state.accepted_slot = "2026-08-04T11:00:00+05:30"
    state.slot_status = "accepted"

    verdict = SimpleNamespace(reason="out_of_hours", slot=None, anchor_day=None)
    ts = SimpleNamespace(readback_confirmed=False)
    sig: dict = {}

    _close_turn(
        state,
        ts,
        verdict,
        claimed=True,
        user_text="ठीक है ठीक है मैंने बोला Wednesday को छह बजे का slot",
        sig=sig,
    )

    assert state.locked_slot is None, "locked a slot the lead was trying to change"
    assert state.slot_status == "out_of_hours"
    assert not sig.get("readback_confirmed")


def test_a_plain_affirmation_still_locks():
    """The guard keys on the lead NAMING a time. A bare "haan theek hai" is a confirmation
    and must still close the call — this is the turn the whole call exists to reach."""
    from types import SimpleNamespace

    from roma.controller.state import CallState
    from roma.controller.turn import _close_turn

    state = CallState(phase="p7_close")
    state.accepted_slot = "2026-08-04T11:00:00+05:30"
    state.slot_status = "accepted"
    sig: dict = {}

    _close_turn(
        state,
        SimpleNamespace(readback_confirmed=False),
        SimpleNamespace(reason="unclear", slot=None, anchor_day=None),
        claimed=False,
        user_text="haan theek hai",
        sig=sig,
    )

    assert state.locked_slot == "2026-08-04T11:00:00+05:30"
    assert state.slot_status == "locked"
    assert sig["readback_confirmed"]


def test_a_resolvable_revision_still_moves_the_slot():
    from types import SimpleNamespace

    from roma.controller.state import CallState
    from roma.controller.turn import _close_turn

    state = CallState(phase="p7_close")
    state.accepted_slot = "2026-08-04T11:00:00+05:30"
    state.slot_status = "accepted"

    from datetime import datetime
    from zoneinfo import ZoneInfo

    new = datetime(2026, 8, 5, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    _close_turn(
        state,
        SimpleNamespace(readback_confirmed=False),
        SimpleNamespace(reason="ok", slot=new, anchor_day=None),
        claimed=True,
        user_text="Wednesday 4 baje",
        sig={},
    )
    assert state.accepted_slot == new.isoformat()
    assert state.slot_status == "accepted"
    assert state.locked_slot is None
