"""Roma may not announce a booking the machine did not make.

Live call CA1bf16a (2026-07-26). The prompt said "Confirm kuch mat karo"; Roma said
"Tuesday subah gyaarah baje ko aap aa rahi ho, ye confirm ho gaya"; the teardown said
`phase=p5_pivot won=False`. Third call in a row, second since the SLOT STATUS line was
added — so the rule moved from the prompt into a gate on the pre-TTS path.
"""

import pytest

from roma.controller.confirmguard import (
    CONFIRMABLE_STATUSES,
    SAFE_REOFFER_LINE,
    is_phantom_confirmation,
    safe_confirmation,
)

LIVE_LINE = "Tuesday subah gyaarah baje ko aap aa rahi ho, ye confirm ho gaya."


def test_the_live_regression_is_blocked():
    assert is_phantom_confirmation(LIVE_LINE, "unclear") is True
    assert safe_confirmation(LIVE_LINE, "unclear") == SAFE_REOFFER_LINE


@pytest.mark.parametrize("status", ["none", "unclear", "in_past", "out_of_hours"])
def test_no_status_short_of_accepted_may_confirm(status):
    assert is_phantom_confirmation("Aapki visit confirm ho gayi.", status) is True


@pytest.mark.parametrize("status", sorted(CONFIRMABLE_STATUSES))
def test_a_real_booking_is_spoken_untouched(status):
    """The gate must not block the one turn the whole call exists to produce."""
    line = "Toh Monday, sattaais July, shaam paanch baje — Vadodara. Confirm hai."
    assert is_phantom_confirmation(line, status) is False
    assert safe_confirmation(line, status) == line


def test_an_unknown_status_fails_towards_not_confirming():
    """An allowlist, not a denylist: a status added later must not silently unlock the
    one sentence that can cost a lead a wasted trip."""
    assert is_phantom_confirmation("visit confirm ho gayi", "some_new_status") is True


def test_an_ordinary_turn_is_never_touched():
    for line in [
        "Aapne padhai kahan tak ki hai?",
        "Faculty saare working professionals hain, abhi industry mein kaam kar rahe hain.",
        "Monday shaam paanch baje ya Tuesday subah gyaarah baje — kaunsa theek rahega?",
        "",
    ]:
        assert is_phantom_confirmation(line, "none") is False
        assert safe_confirmation(line, "none") == line


def test_gujarati_and_devanagari_confirmations_are_caught():
    """Roma answers in the lead's language, so her own claim comes back in Gujarati as
    often as not. A romanized-only cue set would catch nothing on a Gujarati call."""
    assert is_phantom_confirmation("તમારી visit કન્ફર્મ થઈ ગઈ છે.", "none") is True
    assert is_phantom_confirmation("आपकी visit पक्की हो गई.", "unclear") is True


def test_matching_is_on_tokens_not_substrings():
    """`\\w` splits Indic combining marks and a substring test fires inside unrelated
    words — the two failure modes docs/04 already learned the hard way."""
    assert is_phantom_confirmation("Ye course confirmatory nahi hai", "none") is False


def test_the_substitution_claims_nothing_and_ends_on_a_question():
    """A router, not a censor (docs/04): the replacement has to keep the turn moving, or
    the gate trades a phantom booking for a dead conversation."""
    assert SAFE_REOFFER_LINE.strip().endswith("?")
    assert not is_phantom_confirmation(SAFE_REOFFER_LINE, "none")


def test_the_substitution_survives_the_pre_tts_filter():
    """It is still Roma's line and still goes through docs/04. A substitution the filter
    then blocks would land the lead on HARD_FAIL_LINE."""
    from roma.guardrails import safe_output

    assert safe_output(SAFE_REOFFER_LINE) == SAFE_REOFFER_LINE


def test_the_guard_never_takes_the_turn_down_with_it():
    """Fail-safe like `safe_output`: if the gate itself breaks, the line still gets said.
    A guard that can silence Roma is worse than the bug it guards."""

    class _Exploding(str):
        def __iter__(self):
            raise RuntimeError("boom")

    assert safe_confirmation(_Exploding("kuch bhi"), "none") is not None


def test_a_goodbye_with_nothing_booked_is_held():
    from roma.controller.confirmguard import is_premature_signoff

    for line in (
        "Dhanyavaad!",
        "Theek hai ji, milte hain.",
        "धन्यवाद, मिलते हैं",
        "આભાર, મળીએ",
    ):
        assert is_premature_signoff(line, "none"), line
        assert is_premature_signoff(line, "unclear"), line


def test_a_goodbye_after_a_real_lock_is_allowed():
    """The whole point is to reach this line, not to make it unreachable."""
    from roma.controller.confirmguard import is_premature_signoff

    assert not is_premature_signoff("Theek hai ji, milte hain.", "locked")


def test_accepted_is_not_enough_to_say_goodbye():
    """`accepted` means the lead named a time; `locked` means they confirmed it read back.
    docs/03 makes the readback the win condition, and signing off at `accepted` is how the
    earlier calls ended with a lead who thought they had an appointment."""
    from roma.controller.confirmguard import is_premature_signoff

    assert is_premature_signoff("Dhanyavaad, milte hain!", "accepted")


def test_the_gate_releases_after_the_cap_so_a_call_can_still_end():
    """Hard rule six: a lead who is busy or simply will not book gets one alternative and a
    polite close. A gate that can never be satisfied would hold them on the phone, which is
    a worse failure than the one it prevents."""
    from roma.controller.confirmguard import SIGNOFF_ATTEMPT_CAP, safe_close

    line = "Theek hai ji, milte hain."
    for attempt in range(SIGNOFF_ATTEMPT_CAP):
        assert safe_close(line, "none", attempt) != line, f"attempt {attempt} slipped through"
    assert safe_close(line, "none", SIGNOFF_ATTEMPT_CAP) == line, "the call can never end"


def test_the_hold_line_trips_neither_gate_and_survives_the_filter():
    """It is still Roma's line. It must not contain a confirmation cue (the other gate runs
    first and would not re-screen it), nor a sign-off cue (this gate would catch its own
    output), nor anything docs/04 blocks."""
    from roma.controller.confirmguard import (
        SAFE_HOLD_LINE,
        is_phantom_confirmation,
        is_premature_signoff,
    )
    from roma.guardrails import safe_output

    assert not is_phantom_confirmation(SAFE_HOLD_LINE, "none")
    assert not is_premature_signoff(SAFE_HOLD_LINE, "none")
    assert safe_output(SAFE_HOLD_LINE) == SAFE_HOLD_LINE
    assert SAFE_HOLD_LINE.strip().endswith("?")


def test_the_signoff_gate_never_takes_the_turn_down_with_it():
    from roma.controller.confirmguard import safe_close

    class _Exploding(str):
        def __iter__(self):
            raise RuntimeError("boom")

    assert safe_close(_Exploding("kuch bhi"), "none", 0) is not None


def test_asking_for_a_visit_time_before_the_offer_phase_is_held():
    from roma.controller.confirmguard import is_premature_time_talk

    assert is_premature_time_talk(
        "Subah das ya gyaarah baje ka time theek rahega?", "p2_discover"
    )
    assert is_premature_time_talk("Aap kab aa sakte hain?", "p3_value")
    assert is_premature_time_talk("Aap subah free rehte hain ya shaam ko?", "p2_discover")


def test_the_offer_phases_may_talk_about_time():
    """P5 and P7 carry the OFFER line. Booking is the whole job there."""
    from roma.controller.confirmguard import is_premature_time_talk

    assert not is_premature_time_talk(
        "Gyaarah baje ya paanch baje — kaunsa theek rahega?", "p5_pivot"
    )
    assert not is_premature_time_talk("Toh kal gyaarah baje, theek hai?", "p7_close")


def test_the_gate_does_not_eat_ordinary_course_answers():
    """The expensive failure mode. A gate that swallows P3 content would cost the exact
    thing this call was missing — and "chaar se chhe mahine" is a duration, not a clock."""
    from roma.controller.confirmguard import is_premature_time_talk

    for line in (
        "Ye course chaar se chhe mahine ka hai, basic se advance tak.",
        "Batch mein das-baarah log hi hote hain. Aur kya jaanna chahenge?",
        "Branch subah das se shaam chhe baje tak khuli rehti hai.",
        "SEO, social media marketing aur Google Ads — teen module. Kaunsa interesting lagta hai?",
    ):
        assert not is_premature_time_talk(line, "p3_value"), line


def test_the_time_talk_substitution_trips_no_other_gate():
    from roma.controller.confirmguard import (
        SAFE_COURSE_LINE,
        is_phantom_confirmation,
        is_premature_signoff,
        is_premature_time_talk,
    )
    from roma.guardrails import safe_output

    assert not is_premature_time_talk(SAFE_COURSE_LINE, "p2_discover")
    assert not is_phantom_confirmation(SAFE_COURSE_LINE, "none")
    assert not is_premature_signoff(SAFE_COURSE_LINE, "none")
    assert safe_output(SAFE_COURSE_LINE) == SAFE_COURSE_LINE


def test_every_fact_spent_steer_trips_no_other_gate_either():
    """Same invariants as SAFE_COURSE_LINE, for each rotation line. A steer that its own
    guard would catch is an infinite substitution."""
    from roma.controller.confirmguard import (
        SAFE_COURSE_LINES_FACT_SPENT,
        is_phantom_confirmation,
        is_premature_signoff,
        is_premature_time_talk,
    )
    from roma.guardrails import safe_output

    assert SAFE_COURSE_LINES_FACT_SPENT
    for line in SAFE_COURSE_LINES_FACT_SPENT:
        assert not is_premature_time_talk(line, "p2_discover"), line
        assert not is_phantom_confirmation(line, "none"), line
        assert not is_premature_signoff(line, "none"), line
        assert safe_output(line) == line, line


def test_the_steer_stops_naming_the_modules_once_they_are_spent():
    """Call 6d7cc330: Roma named the three modules, then this guard fired TWICE and named
    them again — the same sentence, word for word, both times. The lead heard one fact
    three times. It was blamed on the prompt and `{{said}}` was added to p1/p2 to fix it;
    that could never have worked, because this line is substituted, not generated."""
    from roma.controller.confirmguard import SAFE_COURSE_LINE, steer_line

    assert steer_line(None) == SAFE_COURSE_LINE, "unspent: naming the modules is the point"
    assert steer_line([]) == SAFE_COURSE_LINE
    assert steer_line(["duration"]) == SAFE_COURSE_LINE, "a different fact must not gag it"

    spent = steer_line(["modules"])
    assert spent != SAFE_COURSE_LINE
    for token in ("SEO", "Google Ads", "teen module"):
        assert token not in spent, f"steer re-spent the modules fact via {token!r}"


def test_two_catches_in_one_call_do_not_repeat_the_same_sentence():
    """Both catches on 6d7cc330 returned a byte-identical string. Two identical
    substitutions is what the lead actually complained about."""
    from roma.controller.confirmguard import steer_line

    first = steer_line(["modules"], holds=0)
    second = steer_line(["modules"], holds=1)
    assert first != second


def test_the_steer_survives_junk_in_place_of_the_fact_list():
    """A steer must never cost a turn; unreadable state falls back to the old behaviour."""
    from roma.controller.confirmguard import SAFE_COURSE_LINE, steer_line

    assert steer_line(object()) == SAFE_COURSE_LINE  # type: ignore[arg-type]
    assert steer_line({"modules"}, holds=99) != SAFE_COURSE_LINE, "a set is a valid container"


def test_the_time_gate_catches_english_time_words_too():
    """Live call CA8a85a4f reported `time_talk_holds=0` while Roma asked, in a phase with
    no OFFER line: "timing ke hisaab se kya prefer karenge — weekday evening ya weekend
    batch?" Every cue in the lexicon was Indic, and Roma's register is Hinglish."""
    from roma.controller.confirmguard import is_premature_time_talk

    assert is_premature_time_talk(
        "Timing ke hisaab se kya prefer karenge — weekday evening ya weekend batch?",
        "p3_value",
    )
    assert is_premature_time_talk("Morning slot theek rahega?", "p2_discover")
    assert not is_premature_time_talk(
        "Har module AI tools ke saath sikhaya jaata hai. Kya aur jaanna chahenge?", "p3_value"
    )


def test_tay_hai_is_a_phantom_confirmation():
    """Live call CA00417672. The machine refused the time (`slot_status=in_past`) and Roma
    said this; `is_phantom_confirmation` returned False and the lead was told a refused slot
    was booked. `tay` is the codebase's OWN word for settled — `state.py` writes "slot tay ho
    chuka hai" into the prompt — and it was missing from the cue set."""
    from roma.controller.confirmguard import is_phantom_confirmation

    line = "Aapka visit Vadodara branch mein tay hai dopehar do baje ko."
    assert is_phantom_confirmation(line, "in_past") is True
    assert is_phantom_confirmation(line, "locked") is False


def test_tay_karna_baaki_hai_is_not_a_confirmation():
    """THE trap, and the reason this is a phrase and not a token. `tay` alone is as often a
    denial as a claim: the gate's own `SAFE_HOLD_LINE` says "visit ka time abhi tay karna
    baaki hai" — still to be decided. A bare token cue would make the guard catch its own
    substitution, which is the exact bug the SAFE_REOFFER_LINE comment records."""
    from roma.controller.confirmguard import (
        SAFE_HOLD_LINE,
        SAFE_REOFFER_LINE,
        is_phantom_confirmation,
    )

    assert is_phantom_confirmation(SAFE_HOLD_LINE, "in_past") is False
    assert is_phantom_confirmation(SAFE_REOFFER_LINE, "in_past") is False
    assert is_phantom_confirmation("Visit ka time abhi tay karna baaki hai.", "none") is False


def test_the_phrase_cues_carry_indic_spellings():
    """Roma answers in Hindi and her line comes back in Devanagari as often as romanized."""
    from roma.controller.confirmguard import is_phantom_confirmation

    assert is_phantom_confirmation("आपकी visit तय है दोपहर दो बजे।", "in_past") is True


def test_a_phrase_only_matches_consecutive_tokens():
    """Scattered words are not the phrase. "tay" early and "hai" late is ordinary Hinglish."""
    from roma.controller.confirmguard import is_phantom_confirmation

    assert is_phantom_confirmation(
        "Tay karne ke liye counsellor se baat karni hai.", "none"
    ) is (False)


def test_the_signoff_guard_stands_down_when_the_lead_asks_the_call_to_stop():
    """Live call 049f0dc1 (2026-08-01). The lead said "aap meri madad mat karo" and then
    "aap chale jao yahan se". Roma tried to close politely — exactly what hard rule 6 asks
    for ("BUSY OR ANNOYED: offer one alternative, then close politely. Never push twice").

    `safe_close` overrode her and substituted `SAFE_HOLD_LINE`, a booking push, and would
    have done it three times over (`SIGNOFF_ATTEMPT_CAP`). The guard was written for a lead
    who is still engaged and a model that says goodbye too early; it had no concept of a
    lead who wants the call ENDED, so it made the machine pushy on the lead's behalf.
    """
    from roma.controller.confirmguard import SAFE_HOLD_LINE, safe_close

    goodbye = "Theek hai ji, milte hain."
    # Unchanged behaviour: still engaged, no lock -> the sign-off is held.
    assert safe_close(goodbye, "none", 0) == SAFE_HOLD_LINE
    # The fix: the lead asked to stop, so the close goes out untouched.
    assert safe_close(goodbye, "none", 0, wants_out=True) == goodbye
    # And it does not decay with attempts — the veto holds on every later turn too.
    assert safe_close(goodbye, "none", 2, wants_out=True) == goodbye


def test_the_dismissal_detector_catches_what_the_lead_actually_said():
    """Transcribed verbatim off 049f0dc1. `defers_the_call` missed both — it only knows
    "not right now" — so the sign-off guard had nothing to stand down on."""
    from roma.controller.confirmguard import lead_wants_out

    for said in (
        "आप चले जाओ यहाँ से",
        "आप मेरी मदद मत करो",
        "मुझे interest nahi hai",
        "aap chale jao",
        "phone rakho",
        "pareshan mat karo",
    ):
        assert lead_wants_out(said), f"missed a dismissal: {said!r}"


def test_the_dismissal_detector_does_not_fire_on_a_complaint_or_an_objection():
    """The precision half, and the reason this is not `_is_refusal_only`: that helper
    returns True for ANY negation without time evidence, which includes
    "maine aapko kuch bola hi nahi hai" — a lead complaining they were MISQUOTED, said four
    times on 049f0dc1. Closing the call on that would lose a lead who is still talking.

    A deferral is also not a dismissal: "abhi busy hoon" means call back, not go away."""
    from roma.controller.confirmguard import lead_wants_out

    for said in (
        "मैंने आपको कुछ बोला ही नहीं है",
        "मैंने आपको बोला ही नहीं है",
        "abhi busy hoon",
        "fees kitni hai",
        "nahi nahi, doosra wala",
        "mujhe course ke baare mein bataiye",
    ):
        assert not lead_wants_out(said), f"false positive on: {said!r}"


def test_the_hold_reads_the_slot_back_instead_of_re_asking_for_it():
    """Call e1fff5ee. The machine had ACCEPTED Monday 3 August 2pm and was only waiting on
    the readback. The hold fired anyway with the no-slot line, so Roma said, inside one
    turn: "Monday, 3 August ko do baje slot tay ho chuka hai ... visit ka time abhi tay
    karna baaki hai." She contradicted her own booking, and the lead had nothing to affirm,
    so the slot never locked and the guard fired three times."""
    from roma.controller.confirmguard import SAFE_HOLD_LINE, hold_line

    held = hold_line("accepted", "Monday, 3 August ko do baje")
    assert held != SAFE_HOLD_LINE
    assert "Monday, 3 August ko do baje" in held
    assert "tay karna baaki" not in held, "re-asked for a time the machine already has"
    assert held.strip().endswith("?"), "a hold must hand the turn back"


def test_the_hold_still_asks_for_a_time_when_there_is_none():
    from roma.controller.confirmguard import SAFE_HOLD_LINE, hold_line

    assert hold_line("none", "") == SAFE_HOLD_LINE
    assert hold_line("accepted", "") == SAFE_HOLD_LINE, "no slot text: nothing to read back"
    assert hold_line() == SAFE_HOLD_LINE


def test_no_hold_line_says_ek_minute_ji():
    """The lead asked for this phrase to stop twice — once when the pacer said it, and
    again when this guard did. It is not allowed back in by either door."""
    from roma.controller.confirmguard import SAFE_HOLD_LINE, hold_line

    for line in (SAFE_HOLD_LINE, hold_line("accepted", "Wednesday, 5 August ko do baje")):
        low = line.casefold()
        assert "ek minute" not in low, line
        assert "ek second" not in low, line
        assert "ek sec" not in low, line


def test_the_readback_hold_trips_no_other_gate():
    from roma.controller.confirmguard import (
        hold_line,
        is_phantom_confirmation,
        is_premature_signoff,
    )
    from roma.guardrails import safe_output

    held = hold_line("accepted", "Monday, 3 August ko do baje")
    assert not is_phantom_confirmation(held, "accepted")
    assert not is_premature_signoff(held, "accepted")
    assert safe_output(held) == held


def test_safe_close_passes_the_slot_through_to_the_hold():
    from roma.controller.confirmguard import safe_close

    out = safe_close(
        "Dhanyavaad, milte hain!", "accepted", 0, slot="Monday, 3 August ko do baje"
    )
    assert "Monday, 3 August ko do baje" in out


def test_no_canned_substitution_anywhere_says_ek_second_or_ek_minute():
    """One assertion over EVERY canned line this module can speak. The phrase came back
    twice from a door nobody was watching — the pacer, then SAFE_HOLD_LINE, then this
    would have been the third. Enumerate the doors instead of fixing them one at a time."""
    from roma.controller import confirmguard

    canned = [
        confirmguard.SAFE_REOFFER_LINE,
        confirmguard.SAFE_HOLD_LINE,
        confirmguard.SAFE_COURSE_LINE,
        confirmguard.SAFE_READBACK_HOLD_LINE.format(slot="Monday do baje"),
        *confirmguard.SAFE_COURSE_LINES_FACT_SPENT,
    ]
    for line in canned:
        low = line.casefold()
        assert "ek second" not in low, line
        assert "ek minute" not in low, line
        assert "ek sec" not in low, line


def test_an_invented_refusal_is_blocked():
    """Call bde258d1, both lines verbatim. 4pm is inside the 10-18 window and Saturday was
    never refused by the machine — and eight turns later she booked the lead at Friday 4pm.
    Hard rule 8 forbids this in words; the words did not hold."""
    from roma.controller.confirmguard import SAFE_AVAILABILITY_LINE, safe_availability

    for line in (
        "Mujhe khed hai, lekin shaam 4 baje ka timing unfortunately available nahi hai.",
        "Nihit ji. Mujhe khed hai lekin Saturday ka slot nahi hai.",
    ):
        assert safe_availability(line, "none") == SAFE_AVAILABILITY_LINE, line


def test_a_real_refusal_is_left_alone():
    """When the machine DID refuse the time, saying so is hard rule 8 working, not failing."""
    from roma.controller.confirmguard import safe_availability

    line = "Us waqt branch band hoti hai — wo slot available nahi hai."
    for status in ("out_of_hours", "in_past"):
        assert safe_availability(line, status) == line, status


def test_ordinary_lines_are_not_read_as_refusals():
    from roma.controller.confirmguard import is_phantom_denial

    for line in (
        "Koi fixed slot nahi hota, aap koi bhi time bata dijiye.",
        "Monday gyaarah baje ya paanch baje — kaunsa theek rahega?",
        "Faculty saare working professionals hain.",
        "",
    ):
        assert not is_phantom_denial(line, "none"), line


def test_the_availability_substitution_trips_no_other_gate():
    """It carries no '?' on purpose, so the time-talk guard cannot catch it in a phase
    without an OFFER line."""
    from roma.controller.confirmguard import (
        SAFE_AVAILABILITY_LINE,
        is_phantom_confirmation,
        is_phantom_denial,
        is_premature_signoff,
        is_premature_time_talk,
    )
    from roma.guardrails import safe_output

    line = SAFE_AVAILABILITY_LINE
    assert not is_premature_time_talk(line, "p2_discover")
    assert not is_phantom_confirmation(line, "none")
    assert not is_premature_signoff(line, "none")
    assert not is_phantom_denial(line, "none"), "the guard would catch its own output"
    assert safe_output(line) == line


def test_the_reoffer_does_not_race_the_offer_she_is_about_to_make():
    """Call c9be521d, one turn, verbatim from the TTS log:

        "Abhi wo time final nahi hua hai. Aapko subah ka time theek rahega ya shaam ka?
         Monday, 3 August ko shaam 5 baje ya Tuesday, 4 August ko subah 11 baje ... Kaunsa?"

    The substitution opened a subah/shaam axis and her own next sentence immediately asked a
    different time question. The lead: "यहां पे थोड़ा loop का error है, आपने time के बारे में
    बार बार बोला है"."""
    from roma.controller.confirmguard import (
        SAFE_REOFFER_LINE,
        SAFE_REOFFER_LINE_IN_OFFER,
        reoffer_line,
    )

    for phase in ("p5_pivot", "p7_close"):
        got = reoffer_line(phase)
        assert got == SAFE_REOFFER_LINE_IN_OFFER, phase
        assert "?" not in got, "a second question in a turn that already asks one"
        assert "subah" not in got.casefold()

    # Outside the offer phases there is no competing question, so it still asks one.
    for phase in ("p2_discover", "p3_value", ""):
        assert reoffer_line(phase) == SAFE_REOFFER_LINE, phase
    assert SAFE_REOFFER_LINE.strip().endswith("?")


def test_the_offer_phase_reoffer_trips_no_other_gate():
    from roma.controller.confirmguard import (
        SAFE_REOFFER_LINE_IN_OFFER,
        is_phantom_confirmation,
        is_phantom_denial,
        is_premature_signoff,
        is_premature_time_talk,
    )
    from roma.guardrails import safe_output

    line = SAFE_REOFFER_LINE_IN_OFFER
    assert not is_phantom_confirmation(line, "none"), "the guard would catch its own output"
    assert not is_premature_time_talk(line, "p2_discover")
    assert not is_premature_signoff(line, "none")
    assert not is_phantom_denial(line, "none")
    assert safe_output(line) == line


def test_safe_confirmation_routes_on_the_phase():
    from roma.controller.confirmguard import SAFE_REOFFER_LINE_IN_OFFER, safe_confirmation

    claim = "Aapki visit confirm ho gayi hai."
    assert safe_confirmation(claim, "none", "p5_pivot") == SAFE_REOFFER_LINE_IN_OFFER
    assert safe_confirmation(claim, "none", "p2_discover") != SAFE_REOFFER_LINE_IN_OFFER
    # Back-compat: the phase is optional and defaults to the asking variant.
    assert safe_confirmation(claim, "none") != SAFE_REOFFER_LINE_IN_OFFER
