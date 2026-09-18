"""CallState datum (docs/03, docs/06): discovery-slot ordering, prompt-var view,
and the checkpoint round-trip that backs resume-on-drop."""

from roma.domain.conversation.state import _NO_KNOWN_LINE, DISCOVERY_ORDER, CallState


def test_the_name_is_the_first_thing_asked_for():
    """Roma is inbound — the caller rang us and nobody has told us who they are, so the name
    is a discovery slot like any other and it comes first."""
    assert DISCOVERY_ORDER[0] == "lead_name"
    assert CallState().next_discovery_slot() == "lead_name"


def test_next_discovery_slot_walks_fixed_order():
    s = CallState()
    assert s.next_discovery_slot() == "lead_name"
    s.lead_name = "Asha"
    assert s.next_discovery_slot() == "current_status"
    s.current_status = "job"
    assert s.next_discovery_slot() == "education"
    s.education = "12th"
    assert s.next_discovery_slot() == "passing_year"


def test_next_discovery_slot_skips_already_filled_gaps():
    s = CallState(lead_name="Asha", education="12th", passing_year="2020", city="Vadodara")
    assert s.next_discovery_slot() == "current_status"


def test_next_discovery_slot_none_when_all_filled():
    s = CallState(
        lead_name="Asha",
        education="12th",
        passing_year="2020",
        current_status="student",
        city="Vadodara",
    )
    assert s.next_discovery_slot() is None
    assert s.filled_discovery_count() == len(DISCOVERY_ORDER)


def test_a_slot_the_caller_will_not_answer_stops_blocking_the_ones_behind_it():
    """The pointer drives EXTRACTION, not the question. Before the cap, a caller who would
    not give their name left it pinned on `lead_name` while the model moved on, so every
    later answer was extracted against a field nobody had been asked about — and P2 filled
    nothing at all. Name-first is what made that fatal rather than merely wasteful."""
    from roma.domain.conversation.state import SLOT_ATTEMPT_CAP

    s = CallState()
    for _ in range(SLOT_ATTEMPT_CAP):
        assert s.next_discovery_slot() == "lead_name"
        s.record_slot_attempt("lead_name")
    assert s.next_discovery_slot() == "current_status"
    assert s.lead_name is None  # given up on, not invented


def test_filled_discovery_count():
    s = CallState(education="12th", city="Vadodara")
    assert s.filled_discovery_count() == 2


def test_as_prompt_vars_has_exactly_the_template_keys():
    s = CallState(branch="Ahmedabad", lead_name="Asha")
    assert set(s.as_prompt_vars()) == {
        "branch",
        "lead_name",
        # Outbound dynamic variable, carried from the trigger (dialer.leadstore). Present in
        # the mapping before any fragment reads `{{segment}}` — deliberately: the plumbing
        # ships and is tested first, the prompt rules that use it are still unsigned-off.
        "segment",
        "slot_status",
        "pacing",
        "offered_slots",
        "known",
        # The mirror of `known`: what Roma has already TOLD them. Machine state for the same
        # reason `known` is — the model cannot reliably audit its own earlier turns, and on
        # 049f0dc1 it said the three-module line four times (`controller.facts`).
        "said",
        # What day it IS. Without it she invented one: on e3237622 the lead offered
        # "Wednesday ko aata hoon 2 baje" and she refused with "Aaj Wednesday hai" — on a
        # Saturday. A bookable slot lost to a made-up fact (hard rule 4).
        "today",
    }
    assert s.as_prompt_vars()["branch"] == "Ahmedabad"
    assert s.as_prompt_vars()["lead_name"] == "Asha"


def test_an_uncaptured_name_renders_as_empty_never_the_word_none():
    """`lead_name` is None until P2 captures it (Roma is inbound). A fragment that
    substituted it would otherwise say the literal "None" at a lead."""
    assert CallState().as_prompt_vars()["lead_name"] == ""


def test_a_fresh_call_says_no_slot_is_accepted():
    assert "koi visit slot accept nahi hua" in CallState().as_prompt_vars()["slot_status"]


def test_out_of_hours_tells_roma_to_refuse_and_re_offer():
    """THE regression. It must be an instruction, not a status code — this text lands in a
    system prompt and the model acts on what it can read as an instruction."""
    line = CallState(slot_status="out_of_hours").as_prompt_vars()["slot_status"]
    assert "BAHAR" in line
    assert "confirm bilkul mat karo" in line
    assert "das" in line and "chhe" in line


def test_an_accepted_slot_is_spoken_back_as_a_day_and_a_time():
    s = CallState(slot_status="accepted", accepted_slot="2026-07-27T17:00:00+05:30")
    line = s.as_prompt_vars()["slot_status"]
    assert "Monday 27 July, 5:00 PM" in line
    assert "Readback isi ka karo" in line


def test_locked_wins_over_accepted():
    s = CallState(
        slot_status="locked",
        accepted_slot="2026-07-27T17:00:00+05:30",
        locked_slot="2026-07-28T11:00:00+05:30",
    )
    assert "Tuesday 28 July, 11:00 AM" in s.as_prompt_vars()["slot_status"]


def test_accepted_with_no_readable_slot_falls_back_to_no_slot():
    """Never tell Roma to read back a time she has not been given — she would invent one,
    which is the exact failure this whole mechanism exists to stop."""
    line = CallState(slot_status="accepted", accepted_slot=None).as_prompt_vars()["slot_status"]
    assert "koi visit slot accept nahi hua" in line


def test_an_unknown_status_degrades_to_no_slot_rather_than_raising():
    """A KeyError here would take down the turn — and prompt assembly must never be the
    thing that drops a live call."""
    line = CallState(slot_status="wat").as_prompt_vars()["slot_status"]
    assert "koi visit slot accept nahi hua" in line


def test_slot_status_is_not_checkpointed():
    """It describes the turn that just happened. A resumed call replaying a stale
    "out_of_hours" would have Roma refusing a time nobody proposed (docs/06)."""
    s = CallState(call_sid="CA1", slot_status="out_of_hours")
    assert "slot_status" not in s.to_dict()
    assert CallState.from_dict(s.to_dict()).slot_status == "none"


def test_spoken_slot_is_empty_for_missing_or_unparseable_values():
    from roma.domain.conversation.state import spoken_slot

    assert spoken_slot(None) == ""
    assert spoken_slot("") == ""
    assert spoken_slot("tomorrow evening") == ""


def test_spoken_slot_renders_midnight_and_noon_the_way_a_person_says_them():
    from roma.domain.conversation.state import spoken_slot

    assert "12:00 AM" in spoken_slot("2026-07-27T00:00:00+05:30")
    assert "12:00 PM" in spoken_slot("2026-07-27T12:00:00+05:30")


def test_prompt_vars_feed_the_assembler_without_dangling_placeholders():
    from roma.domain.conversation.prompts import assemble_system_prompt

    s = CallState(branch="Vadodara", lead_name="Asha")
    prompt = assemble_system_prompt(s.as_prompt_vars(), phase="p1_open")
    assert "Vadodara" in prompt and "{{" not in prompt


def test_the_opening_prompt_greets_by_name_when_the_trigger_supplied_one():
    """INVERTED 2026-08-01 with the pivot back to outbound.

    Roma dialled them, so the CRM name is the correct first word — `{{lead_name}}` is back in
    the P1 fragment. The inbound version of this test asserted the opposite for a good reason
    (a stranger who rang us has not told us their name), and that reason no longer holds.
    """
    from roma.domain.conversation.prompts import assemble_system_prompt

    s = CallState(branch="Vadodara", lead_name="Asha")
    assert "Asha" in assemble_system_prompt(s.as_prompt_vars(), phase="p1_open")


def test_the_opening_prompt_never_renders_the_word_none_for_a_missing_name():
    """A triggered call whose CRM record has no name must not greet "Hello None ji".

    `lead_name` is None until captured, and `as_prompt_vars` maps it to "" for exactly this.
    The fragment carries the fallback instruction; this pins the substitution half.
    """
    from roma.domain.conversation.prompts import assemble_system_prompt

    prompt = assemble_system_prompt(CallState(branch="Vadodara").as_prompt_vars(), "p1_open")
    # A bare `"None" not in prompt` fails on the fragment's own instruction not to say it,
    # so assert the SUBSTITUTION SITE instead: `Hello {{lead_name}} ji` must not render a
    # name, and no placeholder may survive.
    assert "Hello None" not in prompt
    assert "{{" not in prompt
    assert "{{lead_name}}" not in prompt

    named = assemble_system_prompt(
        CallState(branch="Vadodara", lead_name="Asha").as_prompt_vars(), "p1_open"
    )
    assert "Hello Asha ji" in named


def test_a_captured_name_reaches_the_model_through_the_known_line():
    """Not through `{{lead_name}}` — through PATA HAI, which is restated from the machine's
    own state every turn and so survives a barge-in (the CA9933275 failure)."""
    from roma.domain.conversation.prompts import assemble_system_prompt

    s = CallState(branch="Vadodara", lead_name="Asha")
    assert "naam Asha" in s.as_prompt_vars()["known"]
    assert "Asha" in assemble_system_prompt(s.as_prompt_vars(), phase="p2_discover")


def test_checkpoint_round_trip_preserves_state():
    s = CallState(
        call_sid="CA123",
        education="12th",
        phase="p5_pivot",
        phase_turn_count=2,
        objection_counts={"fees": 1},
        slots_offered=["Mon 6pm", "Tue 11am"],
        inquiry_confirmed=True,
    )
    restored = CallState.from_dict(s.to_dict())
    assert restored == s


def test_from_dict_ignores_unknown_keys():
    restored = CallState.from_dict({"call_sid": "CA1", "phase": "p2_discover", "legacy": 1})
    assert restored.call_sid == "CA1" and restored.phase == "p2_discover"


def test_no_offer_yet_says_so_rather_than_rendering_a_blank():
    line = CallState().as_prompt_vars()["offered_slots"]
    assert "OFFER:" in line
    assert "{" not in line and "  " not in line


def test_two_offers_render_as_spoken_times():
    s = CallState(slots_offered=["2026-07-28T11:00:00+05:30", "2026-07-28T17:00:00+05:30"])
    line = s.as_prompt_vars()["offered_slots"]
    assert "Tuesday 28 July, 11:00 AM" in line
    assert "Tuesday 28 July, 5:00 PM" in line


def test_the_offer_line_forbids_inventing_a_third_slot():
    """The whole point: a time Roma made up cannot be booked, because the visit is recorded
    against the slots the controller chose."""
    s = CallState(slots_offered=["2026-07-28T11:00:00+05:30", "2026-07-28T17:00:00+05:30"])
    assert "teesra time tab tak mat bolo" in s.as_prompt_vars()["offered_slots"]


def test_a_single_remaining_offer_renders_without_a_dangling_or():
    """Late in the day the calendar may only have one slot left. "X ya " read aloud is
    audibly broken."""
    s = CallState(slots_offered=["2026-07-28T17:00:00+05:30"])
    line = s.as_prompt_vars()["offered_slots"]
    assert "5:00 PM" in line and " ya " not in line


def test_an_unparseable_offer_degrades_to_no_offer():
    """A malformed ISO string must not put a blank into a sentence Roma then says."""
    s = CallState(slots_offered=["not-a-date"])
    assert s.as_prompt_vars()["offered_slots"] == CallState().as_prompt_vars()["offered_slots"]


def test_offers_survive_a_checkpoint_round_trip():
    """Unlike `slot_status`/`elapsed_secs`, the offer IS checkpointed: a slot Roma has
    already SPOKEN is a fact about the call, and a resumed call must not offer different
    times than the lead just heard."""
    s = CallState(call_sid="CA_x", slots_offered=["2026-07-28T11:00:00+05:30"])
    assert CallState.from_dict(s.to_dict()).slots_offered == s.slots_offered


def test_the_known_line_lists_what_the_lead_answered():
    """Roma asked "aap kis saal complete hui thi?", was interrupted, and asked it again in
    the very next turn. The conversation history is in the context but an interruption
    truncates it; the machine's own state is the copy nothing can damage."""
    s = CallState(education="BCom", city="Vadodara")
    known = s.as_prompt_vars()["known"]
    assert "BCom" in known and "Vadodara" in known
    assert "dobara mat poochho" in known


def test_the_known_line_never_names_a_field():
    """`p2_discover` forbids saying "education" or "timing constraint" out loud. A prompt
    that hands Roma the field name is a prompt that gets the field name said."""
    s = CallState(
        education="BCom",
        passing_year="2020",
        current_status="job",
        city="Vadodara",
        timing_constraint="evenings",
    )
    known = s.as_prompt_vars()["known"]
    for field in ("education", "passing_year", "current_status", "timing_constraint"):
        assert field not in known, field


def test_an_empty_profile_says_so_rather_than_rendering_a_dangling_list():
    assert CallState().as_prompt_vars()["known"] == _NO_KNOWN_LINE


def test_the_offer_line_says_the_times_are_not_opening_hours():
    """Roma read the two offers as the branch's opening hours and started asserting them as
    fact — "gyaarah se paanch chalti hai" — then refused a noon visit on that basis."""
    s = CallState(slots_offered=["2026-07-27T11:00:00+05:30", "2026-07-27T17:00:00+05:30"])
    line = s.as_prompt_vars()["offered_slots"]
    assert "koi fixed slot nahi hai" in line
    assert "sirf suggestion" in line


def test_pending_day_is_checkpointed():
    """Which day is under discussion must survive a reconnect, exactly as the discovery
    answers do — otherwise a resumed call re-anchors an hour to the wrong day."""
    s = CallState(call_sid="CA_x", pending_day="2026-07-29")
    assert CallState.from_dict(s.to_dict()).pending_day == "2026-07-29"


def test_an_accepted_slot_removes_the_offer_times_from_the_prompt():
    """THE regression. `p7_close.md` rendered SLOT STATUS ("read back Monday 2:00 PM")
    directly above OFFER ("offer only 11 AM or 5 PM") — two different sets of times in one
    prompt. Roma read the OFFER list and asked "gyaarah baje ya paanch baje?" seven times
    in a row, over a 2 PM the machine had already accepted. `won=False`."""
    s = CallState(
        slots_offered=["2026-07-27T11:00:00+05:30", "2026-07-27T17:00:00+05:30"],
        accepted_slot="2026-07-27T14:00:00+05:30",
        slot_status="accepted",
    )
    v = s.as_prompt_vars()
    assert "11:00 AM" not in v["offered_slots"]
    assert "5:00 PM" not in v["offered_slots"]
    assert "2:00 PM" in v["slot_status"], "the accepted time must still be readable"


def test_a_locked_slot_spends_the_offer_too():
    s = CallState(
        slots_offered=["2026-07-27T11:00:00+05:30"],
        accepted_slot="2026-07-27T14:00:00+05:30",
        locked_slot="2026-07-27T14:00:00+05:30",
        slot_status="locked",
    )
    assert "11:00 AM" not in s.as_prompt_vars()["offered_slots"]


def test_the_offer_survives_a_status_that_is_not_a_booking():
    """The other half: `unclear` / `day_only` / `out_of_hours` all still need the two times
    in front of her — that is how she re-offers."""
    for status in ("none", "unclear", "day_only", "out_of_hours", "in_past"):
        s = CallState(
            slots_offered=["2026-07-27T11:00:00+05:30", "2026-07-27T17:00:00+05:30"],
            slot_status=status,
        )
        assert "11:00 AM" in s.as_prompt_vars()["offered_slots"], status


def test_an_accepted_status_with_no_readable_slot_still_shows_the_offer():
    """`as_prompt_vars` demotes accepted-with-no-slot to `none`, and the offer line must
    follow that demotion — otherwise Roma has no times at all: nothing to read back AND
    nothing to offer."""
    s = CallState(
        slots_offered=["2026-07-27T11:00:00+05:30"], accepted_slot=None, slot_status="accepted"
    )
    assert "11:00 AM" in s.as_prompt_vars()["offered_slots"]


def _state_with_offers():
    from roma.domain.conversation.state import CallState

    s = CallState(call_sid="t", branch="Vadodara", lead_name="ji")
    s.slots_offered = ["2026-07-29T11:00:00+05:30", "2026-07-29T17:00:00+05:30"]
    return s


def test_a_refused_time_withdraws_the_take_any_time_instruction():
    """Live call CA00417672. The lead said "do baje", the resolver returned `in_past`, and the
    prompt then said BOTH "refuse it" (SLOT STATUS) and "koi bhi time das aur chhe ke beech
    ho, use turant maan lo — 'wo slot available nahi hai' kabhi mat bolo" (OFFER). Two
    specific imperatives beat one and Roma said "Aapka visit tay hai dopehar do baje ko."

    Same fix as the `accepted`/`locked` case above: remove the competing instruction."""
    s = _state_with_offers()
    for status in ("in_past", "out_of_hours"):
        line = s._offer_line(status)
        assert "koi bhi time" not in line, status
        assert "kabhi mat bolo" not in line, status
        assert "DOOSRA time" in line, status
        assert "dobara mat maano" in line, status
        assert "gyaarah" in line or "11" in line, status


def test_an_unreadable_time_keeps_the_blanket_instruction():
    """`unclear` and `day_only` mean the machine could not READ the time, not that the time is
    unusable. Withdrawing the blanket rule for those would make Roma refuse bookable slots —
    the exact failure hard rule 8 exists to prevent."""
    s = _state_with_offers()
    for status in ("unclear", "day_only", "none"):
        assert "turant maan lo" in s._offer_line(status), status


def test_an_accepted_slot_still_spends_the_offer():
    """Unchanged by the above — pinned so the refusal branch cannot swallow this case."""
    s = _state_with_offers()
    assert "tay ho chuka hai" in s._offer_line("accepted")
    assert "tay ho chuka hai" in s._offer_line("locked")


def test_lead_wants_out_is_sticky_and_survives_a_checkpoint():
    """Hard rule 6 — "never push twice" — is a statement about the whole call, not one turn.
    A per-turn flag would let `confirmguard.safe_close` start pushing again on the next
    silence, which is the bug it exists to fix (live call 049f0dc1). And a resume must not
    clear it: a lead who asked to stop before the socket dropped has not changed their mind
    because it dropped."""
    from roma.domain.conversation.state import CallState

    s = CallState(branch="Vadodara")
    assert s.lead_wants_out is False
    s.lead_wants_out = True
    assert CallState.from_dict(s.to_dict()).lead_wants_out is True


def test_facts_already_spoken_reach_the_prompt_and_do_not_repeat():
    """Live call 049f0dc1: Roma said the three-module line in FOUR separate turns and
    practical-not-theory in three; the lead's complaint was the loop. `persona.md` already
    forbids it in as many words, which is the point — the rule asks the model to audit its
    own earlier turns, the same thing PATA HAI exists because it cannot do."""
    from roma.domain.conversation.facts import facts_in
    from roma.domain.conversation.state import CallState

    s = CallState(branch="Vadodara")
    assert s.as_prompt_vars()["said"] == "", "nothing said yet must render empty, not a line"

    s.record_facts(facts_in("Course mein teen modules hain: SEO, social media aur Google Ads."))
    s.record_facts(facts_in("Poora practical hai, theory nahi."))
    said = s.as_prompt_vars()["said"]
    assert "teen module" in said and "practical" in said
    assert "dobara mat kehna" in said

    before = list(s.facts_said)
    s.record_facts(facts_in("Teen module hain — SEO, Google Ads."))
    assert s.facts_said == before, "a repeated fact must not be recorded twice"
    assert CallState.from_dict(s.to_dict()).facts_said == before
