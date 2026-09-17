"""Pre-TTS filter processor (roma-guardrail): every LLM sentence is routed through
safe_output() before it reaches Bulbul. The processor's job is to APPLY safe_output
to Roma's spoken TextFrames — the filter's own rules have their 65 tests in
tests/guardrails/. A user TranscriptionFrame (also a TextFrame subclass) must pass
through untouched.
"""

import asyncio

from pipecat.frames.frames import TextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test

from roma.guardrails import safe_output
from roma.telephony.pretts import PreTTSFilterProcessor

BLOCKED = "Fees pachaas hazaar hai."
CLEAN = "Aap kab visit kar sakte ho?"


def _send(proc, frame):
    down, _ = asyncio.run(run_test(proc, frames_to_send=[frame]))
    return down


def test_blocked_line_is_substituted_before_tts():
    proc = PreTTSFilterProcessor()
    down = _send(proc, TextFrame(BLOCKED))
    out = next(f for f in down if isinstance(f, TextFrame))
    assert out.text.strip() == safe_output(BLOCKED)
    assert out.text.strip() != BLOCKED
    assert proc.last_spoken == out.text


def test_clean_line_passes_unchanged():
    proc = PreTTSFilterProcessor()
    down = _send(proc, TextFrame(CLEAN))
    out = next(f for f in down if isinstance(f, TextFrame))
    assert out.text == CLEAN
    assert proc.last_spoken == CLEAN


def test_user_transcription_frame_is_not_filtered():
    proc = PreTTSFilterProcessor()
    down = _send(proc, TranscriptionFrame(BLOCKED, "user", "ts"))
    out = next(f for f in down if isinstance(f, TranscriptionFrame))
    assert out.text == BLOCKED
    assert proc.last_spoken is None


GOODBYE = "Theek hai ji, milte hain."


def _speak(proc, text):
    """Drive ONE frame through and return the text that would reach TTS.

    Deliberately not `run_test`: it sets the processor up and tears it down per call, so
    driving the same instance repeatedly deadlocks (it hung this suite for 120s). These
    tests need state to survive ACROSS turns — that is the whole point of the hold budget —
    so the frame is pushed inline instead, as `test_filler.py` does for the same reason.
    """
    captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    proc.push_frame = _capture
    asyncio.run(proc.process_frame(TextFrame(text), FrameDirection.DOWNSTREAM))
    out = [f for f in captured if isinstance(f, TextFrame)]
    return out[0].text if out else None


def _start_turn(proc):
    """What the LLM service emits before each response. Production always sends it; the
    processor resets its per-turn state on it, so a test driving several TURNS must too."""
    from pipecat.frames.frames import LLMFullResponseStartFrame

    async def _noop(frame, direction=FrameDirection.DOWNSTREAM):
        return None

    proc.push_frame = _noop
    asyncio.run(proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM))


def test_a_goodbye_is_held_while_nothing_is_booked():
    """Live call CA3c7d3c7b ended `no_lock` with Roma saying goodbye of her own accord."""
    proc = PreTTSFilterProcessor(lambda: "none")
    assert _speak(proc, GOODBYE) != GOODBYE
    assert proc.signoff_holds == 1


def test_a_goodbye_passes_once_the_visit_is_locked():
    proc = PreTTSFilterProcessor(lambda: "locked")
    assert _speak(proc, GOODBYE) == GOODBYE
    assert proc.signoff_holds == 0


def test_the_hold_budget_is_per_call_and_eventually_releases():
    """Hard rule six: a lead who will not book still gets a polite close. The counter lives
    on the processor, which is built per websocket connect, so two concurrent calls cannot
    spend each other's budget (docs/08)."""
    from roma.controller.confirmguard import SIGNOFF_ATTEMPT_CAP

    proc = PreTTSFilterProcessor(lambda: "none")
    for attempt in range(SIGNOFF_ATTEMPT_CAP):
        _start_turn(proc)
        assert _speak(proc, GOODBYE) != GOODBYE, f"attempt {attempt} slipped through"
    _start_turn(proc)
    assert _speak(proc, GOODBYE) == GOODBYE, "the call can never end"

    fresh = PreTTSFilterProcessor(lambda: "none")
    assert fresh.signoff_holds == 0, "budget leaked across calls"


def test_a_line_that_both_confirms_and_closes_is_caught():
    """Sentence chunking usually separates these, but not always. The confirmation gate runs
    first and its substitution carries no sign-off cue, so the line is handled once."""
    proc = PreTTSFilterProcessor(lambda: "none")
    spoken = _speak(proc, "Aapki visit confirm ho gayi, milte hain!")
    assert "confirm" not in spoken.casefold()
    assert "milte" not in spoken.casefold()


def test_a_leading_ack_is_dropped_when_a_clip_just_said_it():
    """Live call CA79ed16d: "accha accha repeating twice in call".

    The clip says "Achha…" and her line then opens "Achha, toh aap...". Widening the pool
    and halving the play-rate (the two previous fixes) do nothing about a collision BETWEEN
    the clip and the sentence behind it — the clip cannot know what she is about to say,
    because it plays before the model is asked. So the match is made afterwards, on her
    line."""
    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p5_pivot", lambda: True)
    assert _speak(proc, "Achha, toh aap abhi job kar rahe hain?") == (
        "Toh aap abhi job kar rahe hain?"
    )


def test_the_opener_survives_when_no_clip_played():
    """With no clip there is nothing to duplicate, and an acknowledgement is ordinary
    counsellor speech. Stripping it unconditionally would flatten her."""
    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p5_pivot", lambda: False)
    line = "Achha, toh aap abhi job kar rahe hain?"
    assert _speak(proc, line) == line


def test_only_the_first_sentence_of_a_turn_is_stripped():
    """A mid-turn "Bilkul" is agreement with something the lead just said, not a duplicated
    opener. Only the sentence the clip actually preceded can collide with it."""
    from pipecat.frames.frames import LLMFullResponseStartFrame

    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p5_pivot", lambda: True)
    asyncio.run(proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM))
    assert _speak(proc, "Achha, teen module hain.") == "Teen module hain."
    assert _speak(proc, "Bilkul, wahi hai.") == "Bilkul, wahi hai."


def test_the_same_substitution_is_never_spoken_twice_in_one_turn():
    """Live call CA3bf8d51c. The last thing Roma said, verbatim from the TTS log:

        "Fees aapki situation ke hisaab se counselling mein hi discuss hoti hai — isiliye
         visit best rahega.Pehle main aapko course ke baare mein bata deti hoon — SEO,
         social media marketing aur Google Ads, teen module hain. Aap kis cheez ke baare
         mein zyada jaanna chahenge?Pehle main aapko course ke baare mein bata deti hoon —
         SEO, social media marketing aur Google Ads, teen module hain. Aap kis cheez ke
         baare mein zyada jaanna chahenge?"

    Three sentences of one response each tripped a gate; two got the identical canned line.
    The lead's report was that she repeats herself — and this time it was the guards doing
    it, not the model."""
    from roma.controller.confirmguard import SAFE_COURSE_LINE

    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p3_value")
    _start_turn(proc)
    first = _speak(proc, "Subah das baje aa sakte hain?")
    assert first.strip() == SAFE_COURSE_LINE, "the first catch must still be substituted"
    assert _speak(proc, "Ya shaam ko free rahenge?") is None, "spoke the same line twice"
    assert proc.dupe_drops == 1


def test_two_different_canned_lines_in_one_turn_are_not_both_spoken():
    """Live call CA00417672, verbatim from the TTS log — the confirmation gate and the
    sign-off gate both firing on one response:

        "...tay hai dopehar do baje ko.Ek second ji — abhi wo time final nahi hua hai.
         Aapko subah ka time theek rahega ya shaam ka? Ek minute ji — visit ka time abhi
         tay karna baaki hai. Aapke liye subah theek rahega ya shaam?"

    Two DIFFERENT stock lines that make the same point, so the lead heard it twice. The
    previous version of this guard only caught byte-identical repeats and duly reported
    `dupe_drops=0` on that call while the duplicate was audible."""
    from roma.controller.confirmguard import reoffer_line

    proc = PreTTSFilterProcessor(lambda: "in_past", lambda: "p5_pivot")
    _start_turn(proc)
    first = _speak(proc, "Aapka visit confirm ho gaya hai.")
    # Phase-aware since c9be521d: in an OFFER phase the re-offer is a statement, so it does
    # not race the concrete times Roma is about to name in the same turn.
    assert first.strip() == reoffer_line("p5_pivot")
    assert _speak(proc, "Theek hai ji, milte hain.") is None, "two steers in one turn"
    assert proc.dupe_drops == 1


def test_a_substitution_is_padded_at_both_ends():
    """ "...do baje ko.Ek second ji" — the TTS service concatenates a turn's frames verbatim.
    A trailing space alone cannot fix that join: the sentence IN FRONT is Roma's own line and
    may carry no padding of its own, so the leading space has to be here."""
    proc = PreTTSFilterProcessor(lambda: "none", lambda: "p5_pivot")
    _start_turn(proc)
    out = _speak(proc, "Aapka visit confirm ho gaya hai.")
    assert out.startswith(" ") and out.endswith(" ")


def test_the_next_turn_may_say_it_again():
    """The duplicate is only a duplicate WITHIN a turn. Two minutes later the same steer is
    the right thing to say, and suppressing it for the whole call would leave the second
    catch unguarded."""
    from roma.controller.confirmguard import SAFE_COURSE_LINE

    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p3_value")
    _start_turn(proc)
    assert _speak(proc, "Subah das baje aa sakte hain?").strip() == SAFE_COURSE_LINE
    _start_turn(proc)
    assert _speak(proc, "Shaam ko time hai kya?").strip() == SAFE_COURSE_LINE
    assert proc.dupe_drops == 0


def test_the_steer_reads_spent_facts_off_the_live_call_state():
    """The confirmguard unit tests prove `steer_line` picks the right sentence. This proves
    the processor actually HANDS it the fact list — the wiring is where an inert feature
    comes from, and this repo has had four."""
    from roma.controller.confirmguard import SAFE_COURSE_LINE

    said: list[str] = []
    proc = PreTTSFilterProcessor(
        lambda: "accepted", lambda: "p3_value", None, None, lambda: said
    )

    _start_turn(proc)
    assert _speak(proc, "Subah das baje aa sakte hain?").strip() == SAFE_COURSE_LINE

    said.append("modules")  # Roma has now named the three modules
    _start_turn(proc)
    second = _speak(proc, "Shaam ko time hai kya?").strip()
    assert second != SAFE_COURSE_LINE, "the steer named the modules a second time"
    assert "SEO" not in second and "teen module" not in second


def test_two_steers_after_the_fact_is_spent_are_not_the_same_sentence():
    """`time_talk_holds` is what rotates them; pin that the processor passes it."""
    proc = PreTTSFilterProcessor(
        lambda: "accepted", lambda: "p3_value", None, None, lambda: ["modules"]
    )
    _start_turn(proc)
    first = _speak(proc, "Subah das baje aa sakte hain?").strip()
    _start_turn(proc)
    second = _speak(proc, "Shaam ko time hai kya?").strip()
    assert proc.time_talk_holds == 2
    assert first != second


def test_an_exploding_facts_reader_falls_back_to_the_old_steer():
    from roma.controller.confirmguard import SAFE_COURSE_LINE

    def _boom():
        raise RuntimeError("redis gone")

    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p3_value", None, None, _boom)
    _start_turn(proc)
    assert _speak(proc, "Subah das baje aa sakte hain?").strip() == SAFE_COURSE_LINE


def test_the_first_line_of_a_turn_is_never_dropped():
    """The drop must never be able to produce dead air — that is the one thing docs/04 does
    not allow a catch to do. It cannot, because the turn's history is empty here."""
    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p3_value")
    for _ in range(3):
        _start_turn(proc)
        assert _speak(proc, "Subah das baje aa sakte hain?") is not None
    assert proc.dupe_drops == 0


def test_a_substitution_cannot_fuse_onto_the_next_sentence():
    """ "...isiliye visit best rahega.Pehle main aapko..." — the TTS service concatenates a
    turn's frames verbatim, and the canned lines end on punctuation with no trailing space,
    so two sentences arrived as one unsayable word."""
    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p3_value")
    _start_turn(proc)
    assert _speak(proc, "Subah das baje aa sakte hain?").endswith(" ")


def test_an_acknowledgement_that_is_the_whole_turn_is_left_alone():
    """Dropping it would hand the lead silence, which is the failure the watchdog exists
    for. A short turn is still a turn."""
    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p5_pivot", lambda: True)
    assert _speak(proc, "Achha ji.") == "Achha ji."


def test_the_processor_hands_the_accepted_slot_to_the_hold():
    """Wiring pin. `hold_line` picking the right sentence is worth nothing if the processor
    never passes it the slot — that is how call e1fff5ee got the contradiction."""
    proc = PreTTSFilterProcessor(
        lambda: "accepted",
        lambda: "p7_close",
        None,
        None,
        None,
        lambda: "Monday, 3 August ko do baje",
    )
    _start_turn(proc)
    out = _speak(proc, "Dhanyavaad ji, milte hain!").strip()
    assert "Monday, 3 August ko do baje" in out
    assert "tay karna baaki" not in out
    assert "ek minute" not in out.casefold()


def test_an_exploding_slot_reader_falls_back_to_the_plain_hold():
    from roma.controller.confirmguard import SAFE_HOLD_LINE

    def _boom():
        raise RuntimeError("state gone")

    proc = PreTTSFilterProcessor(
        lambda: "accepted", lambda: "p7_close", None, None, None, _boom
    )
    _start_turn(proc)
    assert _speak(proc, "Dhanyavaad ji, milte hain!").strip() == SAFE_HOLD_LINE


def test_the_duplicate_baje_is_stripped():
    """Call 8b9a9df3, verbatim from the TTS log: "Tuesday, 4 August ko subah 11:00 baje".

    Bulbul runs with enable_preprocessing=True, so it reads "11:00" as "gyaarah baje" and
    then speaks the literal "baje" after it. The lead heard "gyaarah baje baje" and asked
    "बजे बजे दो बार क्यों बोला आपने"."""
    from roma.telephony.pretts import strip_duplicate_baje

    assert strip_duplicate_baje("subah 11:00 baje") == "subah 11 baje"
    assert strip_duplicate_baje("shaam 5:00 baje aayenge") == "shaam 5 baje aayenge"
    assert strip_duplicate_baje("शाम 5:00 बजे") == "शाम 5 बजे"
    # Off the hour: keep the minutes, drop the word, and let the preprocessor voice it.
    assert strip_duplicate_baje("subah 11:30 baje") == "subah 11:30"


def test_ordinary_time_wordings_are_untouched():
    """ "11 baje" is correct Hindi and already reads right — only the colon form doubles."""
    from roma.telephony.pretts import strip_duplicate_baje

    for line in (
        "subah gyaarah baje",
        "shaam 5 baje",
        "Tuesday, 4 August, 11:00 AM — Vadodara.",
        "Branch subah das se shaam chhe baje tak khuli rehti hai.",
        "",
    ):
        assert strip_duplicate_baje(line) == line, line


def test_the_strip_runs_on_what_actually_reaches_tts():
    """Wiring pin: it must apply to the final text, including a canned substitution."""
    proc = PreTTSFilterProcessor(lambda: "accepted", lambda: "p5_pivot")
    _start_turn(proc)
    out = _speak(proc, "Toh Tuesday, 4 August ko subah 11:00 baje — theek hai?")
    assert "11:00 baje" not in out
    assert "11 baje" in out


def test_a_trailing_statement_substitution_is_dropped_not_spoken():
    """Call 02ef08d7, one turn, verbatim:

        "Tuesday 4 August ko subah 11 baje theek rahega? Abhi wo time final nahi hua hai."

    Roma asked, then the guard's statement said the time was not settled — a contradiction
    inside one turn. The blocked sentence still must not air; dropping it is the strongest
    form of that, and docs/04's rule is about dead air, which cannot happen once the turn
    has already produced audible text."""
    proc = PreTTSFilterProcessor(lambda: "none", lambda: "p5_pivot")
    _start_turn(proc)
    first = _speak(proc, "Tuesday 4 August ko subah 11 baje theek rahega?")
    assert first.strip().endswith("?"), "her own question must be spoken"
    second = _speak(proc, "Aapka slot confirm ho gaya hai.")
    assert second is None, "the trailing statement should be dropped, not appended"
    assert proc.dupe_drops == 1


def test_a_first_sentence_substitution_is_still_spoken():
    """The drop is only for a statement landing AFTER she has spoken. If the blocked line is
    the turn's first sentence, dropping it WOULD be dead air."""
    from roma.controller.confirmguard import reoffer_line

    proc = PreTTSFilterProcessor(lambda: "none", lambda: "p5_pivot")
    _start_turn(proc)
    out = _speak(proc, "Aapka slot confirm ho gaya hai.")
    assert out is not None and out.strip() == reoffer_line("p5_pivot")
    assert proc.dupe_drops == 0


def test_a_trailing_substitution_that_asks_is_still_spoken():
    """Only question-free substitutions are dropped — one that hands the turn back is
    doing real work and must survive."""
    proc = PreTTSFilterProcessor(lambda: "none", lambda: "p3_value")
    _start_turn(proc)
    assert _speak(proc, "Aapka background bahut acha hai.") is not None
    out = _speak(proc, "Aapka slot confirm ho gaya hai.")
    assert out is not None and "?" in out
