"""Prompt assembly (docs/11): persona -> hard_rules -> phase, vars substituted.

The order is load-bearing for OpenAI prefix caching; the substitution must leave no
literal `{{...}}` in what reaches the model.
"""

import re

import pytest
from roma.domain.conversation.prompts import assemble_system_prompt, phase_max_tokens
from roma.domain.conversation.state import CallState

CALL_STATE = CallState(branch="Vadodara", lead_name="Test Lead").as_prompt_vars()


def test_assembly_order_persona_then_hard_rules_then_phase():
    prompt = assemble_system_prompt(CALL_STATE, phase="p1_open")
    i_persona = prompt.index("You are Roma")
    i_rules = prompt.index("MONEY")
    i_phase = prompt.index("PHASE: OPEN")
    assert i_persona < i_rules < i_phase


def test_template_vars_substituted_and_none_left_dangling():
    """`{{branch}}` is the only preloaded var left. The name is captured on the call, not
    seeded, and P1 deliberately never speaks it — see the inbound tests below."""
    prompt = assemble_system_prompt(CALL_STATE, phase="p1_open")
    assert "Vadodara" in prompt
    assert "{{" not in prompt and "}}" not in prompt


def test_missing_variable_fails_loud():
    incomplete = {"branch": "Vadodara"}
    with pytest.raises(KeyError):
        assemble_system_prompt(incomplete, phase="p1_open")


def test_phase_max_tokens_is_a_positive_ceiling():
    assert isinstance(phase_max_tokens("p1_open"), int)
    assert phase_max_tokens("p1_open") > 0


def test_the_persona_states_roma_is_a_woman():
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md").lower()
    assert "you are a woman" in persona
    assert "samajh gayi" in persona
    assert "never" in persona and "samajh gaya" in persona


def test_the_persona_forbids_re_asking_and_apologising():
    """Both were live failures: she re-asked education and city after answering noise, and
    opened turns with "mujhe maaf kijiye", which burns the turn and highlights the line."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md").lower()
    assert "never repeat yourself" in persona
    assert "maaf kijiye" in persona


def test_the_no_repeat_rule_covers_facts_not_only_questions():
    """Live call CA00417672: the lead's report was that the course details repeat.

    The rule covered questions only, so she re-served the facts freely — "faculty working
    professionals hain jo abhi industry mein kaam kar rahe hain", then one turn later
    "faculty jo sikhate hain wo khud industry professionals hain", and the placement-guarantee
    answer twice in twenty seconds. Restating a fact in fresh words is still restating it."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md").lower()
    assert "applies to facts" in persona
    assert "already told them" in persona


# Roma's register, linted in both directions (2026-08-08 audit). Two failure modes:
#
#   * masculine SELF-reference — Roma is a woman; "samjha"/"karta hoon" breaks character
#     (live failure, fixed 2026-08-01 in the persona; this pins everything else);
#   * gendered/singular address of the CALLER — persona.md rule: the lead's gender is not
#     ours to guess, so it is always the respectful plural ("aap kar rahe hain"), never
#     "kar rahe ho"/"chahoge". 23 eval reference lines broke this until 2026-08-08.
#
# The old lint covered 7 forms and only `phases/*.md`; every string Roma can actually speak
# is in scope now. Lines that contain "never"/"not" are skipped — the persona teaches by
# negative example, and banning the ban would be self-defeating.
_MASCULINE_SELF = __import__("re").compile(
    r"\b(samjha|samajh gaya|kar raha hoon|karta hoon|bolta hoon|batata hoon|sochta hoon|"
    r"deta hoon|rehta hoon|sakta hoon|chahta hoon|dekhta hoon|"
    r"karunga|bataunga|dekhunga|sochunga|"
    r"समझा|समझ गया|करता हूँ|करता हूं|बोलता हूँ|बोलता हूं|सकता हूँ|सकता हूं|"
    r"करूंगा|बताऊंगा|देखूंगा|रहता हूँ|रहता हूं)\b",
    __import__("re").IGNORECASE,
)
_CALLER_SINGULAR = __import__("re").compile(
    r"\b(kar rahi ho|rehti ho|aayi ho|rahe ho|rehte ho|karoge|chahoge|loge|banoge|aaoge)\b",
    __import__("re").IGNORECASE,
)


def _lintable_lines(text: str):
    for line in text.splitlines():
        low = line.casefold()
        if "never" in low or "not " in low:
            continue  # negative examples teach the rule; see the comment above
        yield line


def test_no_prompt_fragment_uses_a_masculine_self_form_or_singular_address():
    """The persona can name the wrong forms (it is teaching them); the fragments must never
    MODEL them outside a ban, because the model copies register from what it is given."""
    from roma.domain.conversation.prompts import _PROMPTS_DIR

    fragments = [
        *sorted((_PROMPTS_DIR / "phases").glob("*.md")),
        _PROMPTS_DIR / "persona.md",
        _PROMPTS_DIR / "hard_rules.md",
    ]
    for path in fragments:
        for line in _lintable_lines(path.read_text(encoding="utf-8")):
            assert not _MASCULINE_SELF.search(line), (path.name, line)
            assert not _CALLER_SINGULAR.search(line), (path.name, line)


def test_no_runtime_canned_line_breaks_register():
    """Every FIXED string Roma can speak — substitutions, safe lines, fillers, status
    lines — must hold both rules. These bypass the LLM entirely, so no prompt can save
    them; the string itself is the behaviour."""
    from roma.domain.conversation import confirmguard, offtopic, state
    from roma.domain.safety import lexicon
    from roma.realtime import filler

    lines = [
        *[line for bank in offtopic.DEFLECTIONS.values() for line in bank],
        offtopic.CONVERGE_FALLBACK,
        *[offtopic.CONVERGE_PREFIX + q for q in offtopic._SLOT_QUESTIONS.values()],
        *lexicon.SUBSTITUTIONS.values(),
        lexicon.HARD_FAIL_LINE,
        lexicon.PERMITTED_MONEY_LINE,
        *[
            value
            for name, value in vars(confirmguard).items()
            if name.startswith("SAFE_") and isinstance(value, str)
        ],
        *state.SLOT_STATUS_LINES.values(),
        *filler.FILLER_LINES.values(),
        *filler.HOLDING_LINES.values(),
    ]
    assert len(lines) > 10, "the inventory itself went missing"
    for line in lines:
        assert not _MASCULINE_SELF.search(line), line
        assert not _CALLER_SINGULAR.search(line), line


def test_eval_reference_lines_use_the_respectful_plural():
    """The eval scripts are the reference for what a GOOD Roma turn sounds like — and 23 of
    their lines used the banned singular ("padh rahe ho", "chahoge", "loge") until
    2026-08-08, directly contradicting the fragment they score against."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "evals" / "scripts"
    scripts = sorted(root.glob("*.jsonl"))
    assert scripts, "eval scripts directory went missing"
    for path in scripts:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            roma = json.loads(line).get("roma") or ""
            assert not _CALLER_SINGULAR.search(roma), (path.name, roma)
            assert not _MASCULINE_SELF.search(roma), (path.name, roma)


def _fragment(name: str) -> str:
    from roma.domain.conversation.prompts import _read

    return _read(f"phases/{name}.md")


def test_the_close_fragment_carries_no_concrete_date_or_time():
    """Roma said "Toh Wednesday, baais July, shaam paanch baje" — the fragment's own
    example, verbatim — while SLOT STATUS directly above it said "Monday 27 July,
    5:00 PM". A literal date in an example competes with the instruction, and on a small
    model the example wins."""
    text = _fragment("p7_close")
    for leak in ["baais", "Wednesday", "22 July"]:
        assert leak not in text, f"{leak!r} is a concrete date in the readback example"


def test_the_close_fragment_points_at_slot_status_for_every_component():
    text = _fragment("p7_close")
    assert "SLOT STATUS" in text
    assert "from nowhere else" in text


def test_the_discovery_fragment_forbids_speaking_the_field_names():
    """Roma asked "Aapka city kya hai" and "aapka timing constraint kya hai". Those are
    dataclass attribute names, not questions a counsellor asks, and a lead hears the
    difference instantly.

    Asserted as a stated BAN rather than an absence — the same shape as the persona's
    gender rule, which has to name "samajh gaya" in order to forbid it. The fragment must
    say the words; what it must not do is model them as the question to ask."""
    text = _fragment("p2_discover")
    ban = text[text.index("NEVER say") :]
    for label in ["Aapka city kya hai", "aapka timing constraint kya hai"]:
        assert label in ban, f"{label!r} is not named as forbidden"
        assert text.count(label) == 1, f"{label!r} also appears outside the ban"


def test_every_discovery_slot_has_a_spoken_question_not_just_a_label():
    """Naming the field is exactly what produced "aapka passing year kya hai" — the
    fragment has to SHOW the sentence."""
    from roma.domain.conversation.state import DISCOVERY_ORDER

    text = _fragment("p2_discover")
    for slot in DISCOVERY_ORDER:
        assert f"{slot.replace('_', ' ')} →" in text, f"no spoken question for {slot}"
    assert text.count("?") >= len(DISCOVERY_ORDER)


def test_the_discovery_echo_offers_several_openers_not_one_to_copy():
    """Live call CA3c7d3c7b (2026-07-28). The fragment taught the echo with a single worked
    example, `"Achha, toh aap abhi ___ kar rahe hain"`, and gpt-4o did what this repo has
    now watched three models do: it copied the example verbatim. Roma opened consecutive
    turns "Achha, toh abhi aap kya kar rahe hain" and "Achha, toh aap job kar rahe hain",
    on a call where the filler was ALSO saying "Achha…" in front of each one.

    A concrete example in a fragment beats the instruction above it every time, so the fix
    is to vary the example rather than add another prohibition. Several openers, and the
    no-repeat rule stated where the examples are — not three files away in the persona."""
    text = _fragment("p2_discover")
    openers = [o for o in ("Achha", "Samjhi", "Theek hai", "Sahi hai") if o in text]
    assert len(openers) >= 3, f"only {openers} — one example is an example to copy"
    lowered = text.casefold()
    assert "same word" in lowered or "dohraana" in lowered, "no anti-repetition rule"


def test_branch_timings_are_a_hard_rule_not_only_a_phase_hint():
    """Roma offered "subah chaar baje" — 4 AM — with the window stated only in the P5
    fragment. Anything she must never do in ANY phase belongs in hard_rules."""
    from roma.domain.conversation.prompts import _read

    rules = _read("hard_rules.md")
    assert "das baje" in rules and "chhe baje" in rules
    assert "saat baje" in rules


def test_no_fragment_offers_a_slot_the_resolver_would_refuse():
    """`VISIT_HOUR_END` is 18, so "shaam chhe baje" (18:00) is closing time and gets
    refused. A fragment that models it teaches Roma to offer slots she cannot book."""
    from roma.domain.conversation.state import PHASES

    for phase in PHASES:
        text = _fragment(phase)
        assert "shaam chhe baje aa" not in text
        assert "chhe baje ya" not in text


def test_the_persona_bans_the_mishearing_stall():
    """She said "Aapne thoda peeche kuch bola, mujhe samajh nahi aaya" and re-asked a
    question already answered — two rules broken in one turn."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "samajh nahi aaya" in persona
    assert "at most ONCE" in persona


def test_the_persona_bans_an_exclamation_on_every_turn():
    """ "Bohot accha!", "Great!", "Samajh gayi!", "Acha!" opened nearly every turn of the
    live call. Used every turn it stops carrying meaning and reads as a form."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "Bohot accha!" in persona and "EXCLAIM" in persona


def _prompt_at(elapsed: float, phase: str) -> str:
    s = CallState(branch="Vadodara", lead_name="Asha")
    s.elapsed_secs = elapsed
    return assemble_system_prompt(s.as_prompt_vars(), phase=phase)


def test_a_fresh_call_carries_no_time_instruction():
    """An urgency line on turn one would have Roma rushing a lead who just picked up."""
    assert "TIME:" not in _prompt_at(0.0, "p2_discover")


def test_every_phase_fragment_carries_the_pacing_variable():
    """A phase that silently drops it is a phase where the clock stops existing — which is
    exactly where a call would overrun."""
    from roma.domain.conversation.prompts import _read
    from roma.domain.conversation.state import PHASES

    for phase in PHASES:
        assert "{{pacing}}" in _read(f"phases/{phase}.md"), phase


def test_the_urgency_instruction_appears_once_the_call_runs_long():
    from roma.domain.conversation.pacing import CLOSE_SECS, HURRY_SECS, PACING_LINES

    assert PACING_LINES["hurry"] in _prompt_at(HURRY_SECS, "p2_discover")
    assert PACING_LINES["close"] in _prompt_at(CLOSE_SECS, "p5_pivot")


def test_the_cached_prefix_stays_within_its_token_budget():
    """`persona.md` + `hard_rules.md` are re-sent on EVERY turn of every call, so their size
    is multiplied by the turn count and then billed at the cached-input rate. Measured on
    2026-08-01 against `var/roma/spend.jsonl` (121 requests, 17 calls, INR 89.20):

        every 100 tokens in this prefix costs ~INR 0.28 on a 22-turn call

    The prefix was 3,372 tokens and a 22-turn call projected to INR 15.62. Cutting it to
    2,754 — by deleting the live-call anecdotes BEHIND the rules while keeping every
    imperative, with the reasoning moved to docs/11 — brings that to ~INR 13.7, reproduced
    to 0.6% against the real 13-turn call in the ledger.

    Pinned in CHARACTERS, not tokens, deliberately: tiktoken is not a project dependency and
    adding one to guard a budget would be its own kind of cost. ~3.4 chars/token here.

    This is a RATCHET, not a target. Prose grows back one clarification at a time, and
    without a ceiling the saving evaporates silently. If a rule genuinely needs more words,
    raise the ceiling in the same commit and say why — do not delete the test.
    """
    from roma.domain.conversation.prompts import _read

    # Raised twice on 2026-08-01, both times deliberately and in the same commit as the spend:
    #
    #   11,600 -> 12,100  the two rules that failed live on 049f0dc1 — "Samjhi" never
    #                     "samjha" (thinned in the cut, then heard on the call) and NEVER
    #                     TELL THEM WHAT THEY SAID (the lead answered "maine aisa bola hi
    #                     nahi" five times).
    #   12,100 -> 13,100  the per-call target moved 13.38 -> 15.00 at the user's direction,
    #                     because the calls sounded robotic. The headroom went on the rules
    #                     that fight exactly that: the Achha/Bilkul repetition anecdote, vary
    #                     the SHAPE of sentences, and talk-like-a-phone-call-not-a-brochure.
    #
    # A 22-turn call now projects to ~INR 14.94 against the 15.00 target. Note what this
    # budget does NOT buy: latency. Fewer tokens make TTFB faster, so spending here makes
    # calls marginally slower — the lag is the pipeline and the link (measured 1.11s median
    # just to reach OpenAI, and one 64s stall), not the prompt size.
    budget = 13_100  # 12,889 today
    size = len(_read("persona.md")) + len(_read("hard_rules.md"))
    assert size <= budget, (
        f"cached prefix is {size} chars (~{size // 3.4:.0f} tok), over the {budget} ceiling "
        f"by {size - budget}. That is ~INR {(size - budget) / 3.4 / 100 * 0.28:.2f} per "
        "22-turn call, on every call, forever."
    )


def test_the_pacing_line_never_lands_in_the_cached_prefix():
    """persona -> hard_rules must stay byte-identical across a call's turns or OpenAI
    prefix caching stops hitting (docs/11). Only the phase fragment may vary."""
    from roma.domain.conversation.pacing import OVER_SECS
    from roma.domain.conversation.prompts import _read

    for name in ("persona.md", "hard_rules.md"):
        assert "{{pacing}}" not in _read(name), name

    early, late = _prompt_at(0.0, "p5_pivot"), _prompt_at(OVER_SECS, "p5_pivot")
    marker = "PHASE: PIVOT"
    assert early[: early.index(marker)] == late[: late.index(marker)]


def test_the_persona_tells_roma_the_call_is_short():
    """The bands handle the tail of the call; the persona has to stop her opening a
    counselling session in the first minute, when no band has fired yet."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "five minutes" in persona
    assert "TIME line" in persona


def test_the_persona_prefers_a_concrete_detail_over_an_adjective():
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "CONCRETE, NOT ENTHUSIASTIC" in persona
    assert "working professionals" in persona


def test_the_persona_understands_gujarati_without_speaking_it():
    """This test used to assert the OPPOSITE, and the reversal is deliberate — recorded
    here rather than deleted, because the transcript evidence behind the old version is
    still true.

    Every real counsellor in the corpus switches to whatever the lead answers in; one opens
    by asking outright, "Hindi ke Gujarati?". So the persona was written to mirror them.
    Roma then did exactly that on CA8417cd5 and it was the worst-sounding call yet: she
    speaks Gujarati through `Language.HI_IN`/`ishita`, a HINDI voice, in text a small model
    wrote badly ("કoi બાત નહી", "Mumbai — વ્હાલું શહેર!").

    A human counsellor switching languages is fluent in both. This pipeline is not, and
    mirroring a capability it does not have serves the lead worse than not mirroring. What
    survives from the transcripts is the half that IS free: she understands every word of
    the Gujarati, so nobody is ever asked to repeat themselves.
    """
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "UNDERSTAND EVERYTHING, ANSWER IN HINDI" in persona
    assert "Gujarati" in persona


def test_the_value_fragment_asks_the_qualifying_question_the_real_agents_ask():
    """ "skills mate chhe ke placement mate?" comes before the pitch in every transcript
    that reaches a pitch — it changes which two points are worth saying."""
    text = _fragment("p3_value")
    assert "skills ke liye chahiye" in text and "placement ke liye" in text


def test_the_value_fragment_still_refuses_to_quote_outcomes():
    """The real counsellors quote fees, EMI and placement percentages freely. Roma may not
    (docs/04) — the counsellor covers money at the visit. Importing their register must not
    import their numbers."""
    text = _fragment("p3_value")
    assert "Never quote a number" in text
    assert "No outcome numbers" in text

    for leak in ["84", "800", "lakh", "35,000", "48,000", "LPA", "%"]:
        assert leak not in text, f"{leak!r} is an outcome figure the filter would block"

    first_ban = text.index("Never quote a number")
    for named in ["package", "percentage"]:
        assert named in text, f"{named!r} is not named as forbidden"
        assert text.index(named) >= first_ban, f"{named!r} appears before the ban"


def test_the_pivot_and_close_fragments_carry_the_offer():
    from roma.domain.conversation.prompts import _read

    for phase in ("p5_pivot", "p7_close"):
        assert "{{offered_slots}}" in _read(f"phases/{phase}.md"), phase


def test_the_objection_fragment_carries_the_offer_it_reoffers():
    """P6 beat 3 says "RE-OFFER the two times in the OFFER line" — and until 2026-08-08 the
    fragment did not RECEIVE that line: no `{{offered_slots}}`, no `{{slot_status}}`. The
    model was told to re-offer times it could not see, which is an invitation to invent
    them — the exact literal-copying failure the booking fragments are tested against."""
    from roma.domain.conversation.prompts import _read

    p6 = _read("phases/p6_objection.md")
    assert "{{offered_slots}}" in p6
    assert "{{slot_status}}" in p6


def test_p2_and_p6_can_answer_a_structure_question_from_the_prompt_they_hold():
    """The parallel-question trace (2026-08-08 audit): a lead asking "batch kab hoti hai?"
    in P2 got no real answer — the structure facts lived only in the p3/p4 fragments the
    model was not holding, and `{{known}}`/`{{said}}` are purely negative. Both fragments
    now carry the compact approved list, so the question is answered in one line and the
    phase machine still owns every transition."""
    from roma.domain.conversation.prompts import _read

    for phase in ("p2_discover", "p6_objection"):
        text = _read(f"phases/{phase}.md")
        assert "ek se dedh ghanta" in text, phase
        assert "same faculty" in text, phase
        assert "chaar se chhe mahine" in text, phase


def test_the_booking_fragments_name_no_concrete_weekday_or_clock_time():
    """A literal in the fragment is what the model copies — proven twice now, by the p7
    readback date and by this offer. Every day and time Roma proposes must come from a
    rendered variable, never from the prompt text.

    Scoped to the two fragments that propose a VISIT. `p4_structure` legitimately names
    "Saturday aur Sunday" because that is the batch schedule — a fact about the course, not
    a slot the lead can accept — and banning it there would delete real information."""
    import re

    weekday = re.compile(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE
    )
    for phase in ("p5_pivot", "p7_close"):
        stripped = re.sub(r"<[^>]*>", "", _fragment(phase))
        assert not weekday.search(stripped), f"{phase} names a weekday literally"
        assert "gyaarah baje" not in stripped, f"{phase} names a clock time literally"


def test_the_pivot_fragment_forbids_inventing_a_slot():
    text = _fragment("p5_pivot")
    assert "from OFFER and from nowhere else" in text
    assert "Do not invent a slot" in text


def test_the_offer_reaches_the_assembled_pivot_prompt():
    s = CallState(branch="Vadodara", lead_name="Asha")
    s.slots_offered = ["2026-07-28T11:00:00+05:30", "2026-07-28T17:00:00+05:30"]
    prompt = assemble_system_prompt(s.as_prompt_vars(), phase="p5_pivot")
    assert "Tuesday 28 July, 11:00 AM" in prompt
    assert "{{" not in prompt


def test_the_phases_that_must_not_re_ask_carry_the_known_line():
    """Discovery, the pivot and the close all forbid re-asking something answered. The ban
    is only enforceable if the fragment is told what WAS answered."""
    from roma.domain.conversation.prompts import _read

    for phase in ("p2_discover", "p5_pivot", "p7_close"):
        assert "{{known}}" in _read(f"phases/{phase}.md"), phase


def test_hard_rules_separate_the_offer_times_from_the_opening_hours():
    """Roma told a live lead the branch runs "gyaarah se paanch" — the two OFFER times read
    as opening hours — and then refused a noon visit as outside them, with the branch open
    nine to six. Both facts now have to be in the rule that governs every phase."""
    from roma.domain.conversation.prompts import _read

    rules = _read("hard_rules.md")
    assert "SUGGESTION" in rules
    assert "sirf gyaarah ya paanch ka slot hai" in rules
    assert "baarah baje" in rules


_GUJARATI_RANGE = range(0x0A80, 0x0B00)


def _all_prompt_text() -> dict:
    from roma.domain.conversation.prompts import _PROMPTS_DIR

    return {p.name: p.read_text(encoding="utf-8") for p in _PROMPTS_DIR.rglob("*.md")}


def test_no_fragment_tells_roma_to_switch_language():
    """THE regression. One sentence in persona.md produced a whole call in bad Gujarati."""
    for name, text in _all_prompt_text().items():
        low = text.lower()
        assert "switch to gujarati" not in low, name
        assert "mirror them" not in low, name


def test_the_persona_says_answer_in_hindi():
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "ANSWER IN HINDI" in persona
    assert "Never switch language to match theirs." in persona


def test_answering_in_hindi_is_a_hard_rule_not_only_a_persona_hint():
    """Persona-level register guidance is what the model overrode here, so the rule that
    governs every phase carries it too — same treatment as the branch timings."""
    from roma.domain.conversation.prompts import _read

    rules = _read("hard_rules.md")
    assert "ALWAYS ANSWER IN HINDI" in rules


def test_no_fragment_models_a_gujarati_sentence_for_roma_to_copy():
    """Fragments teach by example, and a Gujarati example is an instruction to speak it.
    Gujarati may appear ONLY in persona.md, where it names lead INPUT to be understood."""
    allowed = {"persona.md"}
    for name, text in _all_prompt_text().items():
        if name in allowed:
            continue
        found = [c for c in text if ord(c) in _GUJARATI_RANGE]
        assert not found, f"{name} carries Gujarati script: {''.join(found[:20])!r}"


def test_the_persona_bans_apologising_outright():
    """On CA1652a5e Roma said "Maaf kijiye" four times in five turns and never got back to
    the booking. The ban existed but named only the longer "mujhe maaf kijiye"."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "NEVER APOLOGISE" in persona
    for form in ('"maaf kijiye"', '"sorry"', '"agar koi confusion hua"'):
        assert form in persona, form


def test_the_persona_bans_every_form_of_apology():
    """Two calls, two different apologies, both through a ban that named only the first.
    CA1652a5e: "Maaf kijiye" four times in five turns. CA0460d52: "Mujhe khed hai, lekin
    three PM ka slot available nahi hai" — and three PM WAS available."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "NEVER APOLOGISE, IN ANY WORDS" in persona
    for form in ("mujhe khed hai", "sorry", "maaf kijiye", "samajh nahi paayi"):
        assert form in persona, form


def test_the_close_fragment_gates_the_sign_off_on_the_lock():
    """CA0460d52: Roma read back Monday 5 PM, the lead asked a question about the course
    instead of confirming, and she went straight to "Confirmation WhatsApp par bhej dungi.
    Milte hain!" — signing off on a visit that was never locked. `won=False`."""
    text = _fragment("p7_close")
    assert "DO NOT SIGN OFF UNTIL SLOT STATUS SAYS THE VISIT IS LOCKED" in text
    assert "milte hain" in text.lower()


def test_the_visiting_window_is_ten_to_six_everywhere_it_is_stated():
    """The hours appear in four places and every one of them is spoken to a lead. A window
    corrected in the resolver but not in the prompt is a Roma who refuses times the machine
    accepts — which is exactly what happened when the two OFFER times leaked into her
    description of the opening hours (CA9933275)."""
    from roma.domain.appointments.timeresolve import VISIT_HOUR_END, VISIT_HOUR_START
    from roma.domain.conversation.prompts import _read
    from roma.domain.conversation.state import SLOT_STATUS_LINES

    assert (VISIT_HOUR_START, VISIT_HOUR_END) == (10, 18)
    assert "das" in _read("hard_rules.md")
    assert "das" in SLOT_STATUS_LINES["out_of_hours"]
    assert "das" in _read("phases/p6_objection.md")


def test_the_hard_rules_say_any_time_in_the_window_is_bookable():
    """Roma told a live lead "slot Monday ko sirf subah 11 baje ya shaam paanch baje ka
    hai". Counselling runs 10-6 and a lead may walk in at any point inside it; the two
    OFFER times exist only so the lead is not asked to pick out of thin air."""
    from roma.domain.conversation.prompts import _read

    rules = _read("hard_rules.md")
    assert "no fixed slots" in rules.lower() or "There are no fixed slots" in rules
    assert "take it immediately" in rules


def test_the_hard_rules_ban_volunteering_the_recording():
    """She said "call bhi record ho raha hai" unasked, mid-answer, to a lead who had asked
    what topic they were discussing. Recording disclosure is a compliance line played at
    the start of the call or not at all — it is not hers to make."""
    from roma.domain.conversation.prompts import _read

    rules = _read("hard_rules.md")
    assert "NEVER VOLUNTEER THAT THE CALL IS RECORDED" in rules


def test_the_hard_rules_give_one_answer_for_the_location_question():
    """The lead asked where the branch is three times. She said the location would be
    explained AT the visit — which is not an answer — before eventually offering WhatsApp.
    A lead who does not know where to come will not come."""
    from roma.domain.conversation.prompts import _read

    rules = _read("hard_rules.md")
    assert "address comes on WhatsApp" in rules
    assert "saade paanch se saat" in rules


def test_the_discovery_fragment_never_models_inventing_a_degree():
    """THE regression, and the cause was the fragment's own example.

    It said: React to what they said in three or four words — "Achha, BCom". A small model
    copies the example, so "college tak padha hai" came back as "BCom kiya hai toh" on two
    consecutive live calls. The lead's reply the second time: "khud se kyon generate kara
    rahi hai ki maine B.Com hi kiya hai".

    A degree may now appear ONLY inside the ban that forbids guessing one."""
    text = _fragment("p2_discover")
    cut = text.index("NEVER PUT A DEGREE")
    ban, rest = text[cut:], text[:cut]
    for degree in ("BCom", "BCA"):
        assert degree in ban, f"{degree} must be named as an example of what NOT to guess"
        assert degree not in rest, f"{degree} appears outside the ban, where it is an example"


def test_the_discovery_fragment_asks_the_status_question_with_its_options():
    """ "Aapne padhai kahan tak ki hai?" is too open — it gets "college tak" back, which
    tells the machine nothing and leaves the model filling the gap itself."""
    text = _fragment("p2_discover")
    assert "padh rahe hain, koi course kar rahe hain, ya job kar rahe hain" in text
    assert "too open" in text


def test_the_discovery_fragment_echoes_the_leads_own_word_back():
    """What the user asked for: "accha to aap abhi ___ kar rahe ho", with THEIR word in the
    blank rather than a more specific one Roma picked."""
    text = _fragment("p2_discover")
    assert "toh aap abhi ___ kar rahe hain" in text
    assert "never a more specific version" in text


def test_the_status_slot_is_asked_first():
    """The fragment's order and DISCOVERY_ORDER must agree, or Roma asks one question while
    the machine records the answer against another."""
    from roma.domain.conversation.state import DISCOVERY_ORDER

    assert DISCOVERY_ORDER[0] == "lead_name"
    assert DISCOVERY_ORDER[1] == "current_status"
    text = _fragment("p2_discover")
    assert text.index("lead name →") < text.index("current status →")
    assert text.index("current status →") < text.index("education →")


def test_the_value_and_structure_phases_never_ask_for_a_visit_time():
    """The lead asked "aap mujhe pehle bataiye ki course hai kis liye" and got half a
    sentence plus "aapne kab aana hai, subah diez se baarah?" — a booking push from P3,
    before `p5 offers` had even fired. The pivot is P5's job and P5 has the only times
    Roma is allowed to speak."""
    for phase in ("p3_value", "p4_structure"):
        text = _fragment(phase)
        assert "DO NOT ASK FOR A VISIT DAY OR TIME IN THIS PHASE" in text, phase


def test_the_value_fragment_can_actually_answer_what_the_course_is():
    """ "Ye course kya hai?" is the question this phase exists for. It needs the concrete
    answer in it, not only a menu of situational talking points."""
    text = _fragment("p3_value").lower()
    assert "teen module" in text
    assert "chaar se chhe mahine" in text
    assert "answer what they asked, properly" in text
    assert "keyword research" in text and "conversion tracking" in text


def test_the_structure_fragment_labels_its_timings_as_class_not_visit():
    """These lines came back out of a live call as "saade paanch se saat ke beech" offered
    as a VISIT time — outside branch hours, and not a slot anyone could book."""
    text = _fragment("p4_structure")
    assert "CLASS schedule" in text
    assert "saade paanch se saat" in text


def test_no_prompt_tells_roma_to_say_something_the_filter_blocks():
    """Roma was instructed in three places to say "hundred percent practical" — and the
    pre-TTS filter blocks `percent` outright (PLACEMENT). Every time she followed the
    instruction the guardrail substituted her line, costing the turn.

    The prompt was changed, never the filter: docs/04's rules are the safety net and are not
    loosened to fit a phrase."""
    from roma.domain.conversation.prompts import _PROMPTS_DIR
    from roma.domain.conversation.state import SLOT_STATUS_LINES
    from roma.domain.safety import screen

    example = re.compile(r'^-\s+(?:[^:]{0,40}:\s*)?"([^"]+)"\.?$')
    checked = 0
    for path in sorted(_PROMPTS_DIR.rglob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = example.match(line.strip())
            if not m:
                continue
            checked += 1
            verdict = screen(m.group(1))
            assert verdict.allowed, f"{path.name}: {m.group(1)!r} -> {verdict.reason}"
    assert checked >= 10, f"the example scraper matched only {checked} lines — check it"

    for key, text in SLOT_STATUS_LINES.items():
        assert "offer karo" not in text, f"SLOT_STATUS_LINES[{key}] teaches a blocked word"


def test_the_persona_addresses_the_lead_in_the_gender_neutral_plural():
    """The lead had to say "main ek ladka hoon bro" to correct her, and she answered
    "Mujhe samajh gayi, aap ladka ho" — repeating it back, in broken Hindi.

    Hindi's respectful plural ("aap kar rahe hain") carries no gender, so it is right for
    everyone and there is nothing to decide. The singular "kar rahe ho" / "kar rahi ho"
    forces a guess on every single turn."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "THE LEAD'S GENDER IS NOT YOURS TO GUESS" in persona
    assert "aap kar rahe hain" in persona
    assert "do not repeat it back" in persona


def test_no_fragment_models_a_gendered_singular_for_the_lead():
    """Fragments teach by example. Every question and echo Roma is SHOWN must use the
    plural, or she copies the singular and has to guess."""
    import re as _re

    from roma.domain.conversation.prompts import _PROMPTS_DIR

    singular = _re.compile(r"\b(rahe ho|rahi ho|rehte ho|rehti ho|karoge|chahoge|kahan ho)\b")
    for path in sorted((_PROMPTS_DIR / "phases").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert not singular.search(text), f"{path.name}: {singular.search(text).group(0)!r}"


def test_no_offer_line_means_no_time_talk_is_a_hard_rule():
    """P3's own ban lost to the persona's "YOUR ONE JOB: fix a specific visit day AND
    time". The rule the model can apply mechanically is the presence of the OFFER line,
    which `as_prompt_vars` renders only in the booking phases."""
    from roma.domain.conversation.prompts import _read

    rules = _read("hard_rules.md")
    assert "NO OFFER LINE, NO TIME TALK" in rules
    assert "This rule outranks your one job" in rules


def test_the_value_fragment_goes_deeper_than_the_module_list():
    """CA40f0474: the lead said "mujhe SEO mein zyada interest hai" and got the generic
    three-module sentence back. Naming a module has to be answerable with something about
    THAT module, or the answer reads as a page being recited."""
    text = _fragment("p3_value").lower()
    assert "follow their interest" in text
    for depth in ("keyword research", "audience targeting", "conversion tracking", "ai tools"):
        assert depth in text, depth


def test_the_value_fragment_says_what_they_can_do_afterwards():
    """ "Why should I do this" is answered by what they can DO with it, not by features."""
    text = _fragment("p3_value").lower()
    assert "freelancing" in text
    assert "portfolio" in text


def test_the_value_fragment_sells_placement_support_without_promising_it():
    """The lead should get excited about placement; hard rules one and two still hold."""
    text = _fragment("p3_value")
    assert "Placement support poora milta hai" in text
    assert "Never promise a job" in text
    assert "guaranteed" in text


def test_the_value_phase_has_room_for_a_real_explanation():
    """A 70-word cap bought one thin sentence. The phase where the lead decides needs
    enough to say something worth deciding on."""
    from roma.domain.conversation.machine import P3_MAX_TURNS
    from roma.domain.conversation.prompts import PHASE_WORD_CAPS

    assert PHASE_WORD_CAPS["p3_value"] >= 100
    assert P3_MAX_TURNS >= 4


# --- inbound (docs/decisions.md, LOCKED 2026-07-31) ---------------------------------------


def test_the_opening_fragment_is_an_outbound_dialer_script():
    """INVERTED 2026-08-01: the inbound trunk cannot carry calls, so Roma dials again.

    P1 must identify her and ASK PERMISSION — she interrupted them. It may greet by name,
    because the trigger supplies one (dialer/leadstore). `course_interest` stays gone: Weltec
    sells one course and no fragment reads it.
    """
    text = _fragment("p1_open")
    assert "Weltec" in text
    assert "{{lead_name}}" in text, "outbound greets by name; the trigger supplies it"
    assert "2 minute baat ho sakti hai" in text, "the permission ask is the whole turn"
    assert "REAL NAME" in text, "the model dropped the name on the first live call"
    assert "{{course_interest}}" not in text


def test_the_opening_fragment_still_does_not_ASK_for_the_name():
    """Unchanged by the pivot, for the same reason: extraction is phase-gated to P2, so a
    name given in P1 is heard, echoed and dropped. Outbound we already HAVE the name, which
    makes asking for it worse — it proves nobody read the record before dialling."""
    text = _fragment("p1_open")
    assert "Do not ask their name here" in text
    assert "naam kya hai" not in text


def test_the_opening_fragment_owns_the_greeting_again():
    """INVERTED. The canned opener is inbound's answering line and no longer plays outbound
    (`media.build_media_app` gates it on the lead token), so P1 makes the greeting itself.
    A fragment still saying "you have already said hello" would open the call with silence."""
    text = _fragment("p1_open")
    assert "YOU RANG THEM" in text
    assert "just picked up" in text
    assert "YOU HAVE ALREADY SAID HELLO" not in text


def test_the_discovery_fragment_asks_the_name_first_and_only_once():
    text = _fragment("p2_discover")
    assert "lead name →" in text
    assert "Aapka naam kya hai?" in text
    assert "THE NAME COMES FIRST" in text


def test_the_persona_says_we_dialled_them():
    """The persona used to end 'You already know their name, city, and course interest from
    their WhatsApp chat.' On an inbound call every clause of that is false."""
    from roma.domain.conversation.prompts import _read

    persona = _read("persona.md")
    assert "YOU RANG THEM" in persona
    assert "THEY RANG YOU" not in persona
    # The WhatsApp *chat* reference stays gone — there is no prior conversation to resume.
    # Offering details ON WhatsApp when someone defers is a different thing and is allowed.
    assert "whatsapp chat" not in persona.casefold()


def test_an_empty_variable_does_not_leave_a_doubled_space_in_the_example():
    """`Hello {{lead_name}} ji` must render as `Hello ji`, not `Hello  ji`.

    The Shape line is an example the model copies verbatim, so whitespace damage in it is
    whitespace damage in what Bulbul is asked to speak.
    """
    from roma.domain.conversation.prompts import assemble_system_prompt
    from roma.domain.conversation.state import CallState

    nameless = assemble_system_prompt(CallState(branch="Vadodara").as_prompt_vars(), "p1_open")
    assert "Hello ji" in nameless
    assert "  " not in nameless, "a run of spaces survived rendering"

    named = assemble_system_prompt(
        CallState(branch="Vadodara", lead_name="Asha").as_prompt_vars(), "p1_open"
    )
    assert "Hello Asha ji" in named


def test_the_spoken_opener_matches_the_p1_fragment_word_for_word():
    """Outbound speaks its opener from `opening_line()` instead of generating it, so two
    copies of the same sentence now exist — one in code, one in `p1_open.md`. If they drift,
    the model is told to say one thing and Roma says another, and nothing else would catch it.

    Both live calls that were SILENT end to end (acf8e78f, c5672a23, 2026-08-01) died on the
    FIRST completion stalling — 64s and 18.6s. That turn's text is fully determined, so it
    must not wait on a network round trip to be produced.
    """
    from roma.domain.conversation.prompts import (
        OPENING_LINE_NAMED,
        OPENING_LINE_NAMELESS,
        _read,
        opening_line,
    )

    fragment = _read("phases/p1_open.md")
    assert OPENING_LINE_NAMED.format(name="{{lead_name}}") in fragment, (
        "the code's opener no longer matches the sentence p1_open orders"
    )
    assert opening_line("Nihit").startswith("Hello Nihit ji,")
    # A nameless CRM record must never produce "Hello None ji" or a doubled space.
    for blank in (None, "", "   "):
        assert opening_line(blank) == OPENING_LINE_NAMELESS
        assert "None" not in opening_line(blank)
        assert "  " not in opening_line(blank)


def test_p2_never_tells_roma_to_move_to_the_course_herself():
    """Live regression, call 98aa06a3 (2026-08-04): she pitched the course in P2.

    The fragment's LAST line read "After the city, you are done asking. Move to the
    course." — a bare imperative in the position a model weights most heavily, directly
    contradicting line 40 ("do not pitch anything yet") ten lines above it. She obeyed the
    last one, opening turn two with "Pehle main aapko course ke baare mein bata deti hoon —
    SEO, social media marketing aur Google Ads, teen module hain."

    The lead's own words, two turns later:

        "मैंने course के बारे में आपको पूछा ही नहीं है"
        "आपने मुझसे ये तो पूछा ही नहीं कि मैं कहाँ पढ़ता हूँ कौन सी job करता हूँ"

    `machine.next_phase` owns the P2 -> P3 transition and always has. Telling the model to
    move phases is not merely redundant, it is an invitation to skip discovery entirely.
    """
    from roma.domain.conversation.prompts import _read

    p2 = _read("phases/p2_discover.md").casefold()
    # The IMPERATIVE is what the model obeyed, so that is what is banned — a sentence
    # beginning "move to the course". "never move to the course yourself" contains the same
    # words and is the opposite instruction, which is why a substring check is not enough.
    for sentence in p2.replace("\n", " ").split("."):
        assert not sentence.strip().startswith("move to the course"), (
            "P2 tells Roma to move to the course herself; the machine owns phase transitions"
        )
    # The positive half: it must still say what she may NOT do, or this is just a deletion.
    assert "do not pitch anything yet" in p2
    assert "never move to the course yourself" in p2
