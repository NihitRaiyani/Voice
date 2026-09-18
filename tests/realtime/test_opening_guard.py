"""The opening-turn guard (live regression, call CA21667bf on 2026-07-26).

Roma greeted twice and the lead's real reply queued behind eight seconds of duplicate
audio, because the lead's pickup "Hello" arrived while the opening `LLMRunFrame` was still
generating and the aggregator read it as a complete user turn.

`run_test` cannot drive this: the guard deliberately WITHHOLDS frames, and run_test blocks
waiting for downstream frames that are not coming yet. So process_frame is driven inline —
the same reason `test_media_interruption.py` does it for the TTS sanitizer — with
`enable_direct_mode` so FrameProcessor does not try to schedule on a task manager that was
never set up.
"""

import asyncio

import pytest
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from roma.realtime.opening import OpeningTurnGuard


def _guard(**kw):
    g = OpeningTurnGuard(enable_direct_mode=True, **kw)
    g.captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        g.captured.append(frame)

    g.push_frame = _capture
    return g


def _send(guard, frames):
    async def run():
        for f in frames:
            await guard.process_frame(f, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return guard.captured


def test_a_transcript_before_roma_opens_does_not_start_a_turn():
    """THE regression. Without this the pickup 'Hello' fires a second P1 generation."""
    g = _guard()
    out = _send(g, [TranscriptionFrame("Hello", "lead", "t1")])
    assert out == []


def test_the_words_are_released_after_the_opening_not_discarded():
    """A lead who answers with 'Haan boliye' has told us something docs/03 P1 reads as the
    inquiry confirm. Suppressing the TURN must not discard the WORDS. (A bare "Hello" is
    the other case — see `test_a_bare_hello_is_dropped_not_released`.)"""
    g = _guard()
    _send(g, [TranscriptionFrame("Haan boliye", "lead", "t1"), BotStoppedSpeakingFrame()])
    texts = [f.text for f in g.captured if isinstance(f, TranscriptionFrame)]
    assert texts == ["Haan boliye"]
    assert g.opened and g.held == []


def test_several_held_transcripts_are_released_as_ONE_turn():
    """Re-emitting them separately would hand the aggregator several user turns back to
    back — the same stacking the guard exists to prevent."""
    g = _guard()
    _send(
        g,
        [
            TranscriptionFrame("Hello", "lead", "t1"),
            TranscriptionFrame("haan boliye", "lead", "t2"),
            BotStoppedSpeakingFrame(),
        ],
    )
    released = [f for f in g.captured if isinstance(f, TranscriptionFrame)]
    assert len(released) == 1
    assert released[0].text == "Hello haan boliye"


def test_after_opening_everything_passes_straight_through():
    g = _guard()
    _send(
        g,
        [BotStoppedSpeakingFrame(), TranscriptionFrame("kal shaam theek hai", "lead", "t1")],
    )
    texts = [f.text for f in g.captured if isinstance(f, TranscriptionFrame)]
    assert texts == ["kal shaam theek hai"]
    assert g.held == []


def test_interims_before_opening_are_dropped_not_held():
    """A partial must never start a turn early, and the final supersedes it anyway —
    holding it would duplicate the text in the released turn."""
    g = _guard()
    _send(
        g,
        [
            InterimTranscriptionFrame("Haan bol", "lead", "t1"),
            TranscriptionFrame("Haan boliye", "lead", "t2"),
            BotStoppedSpeakingFrame(),
        ],
    )
    released = [f for f in g.captured if isinstance(f, TranscriptionFrame)]
    assert [f.text for f in released] == ["Haan boliye"]


def test_a_bare_hello_is_dropped_not_released():
    """THE regression. There is nothing in "Hello" to respond to."""
    g = _guard()
    _send(g, [TranscriptionFrame("Hello", "lead", "t1"), BotStoppedSpeakingFrame()])
    assert not [f for f in g.captured if isinstance(f, TranscriptionFrame)]


@pytest.mark.parametrize("text", ["Hello", "hello hello", "Hi", "નમસ્તે", "हेलो", "કેમ છો"])
def test_pickup_noise_in_either_script_is_dropped(text):
    g = _guard()
    _send(g, [TranscriptionFrame(text, "lead", "t1"), BotStoppedSpeakingFrame()])
    assert not [f for f in g.captured if isinstance(f, TranscriptionFrame)]


@pytest.mark.parametrize(
    "text",
    [
        "Haan boliye",
        "હા",
        "जी हाँ",
        "Hello kaun bol raha hai",
        "haan digital marketing ke liye kiya tha",
    ],
)
def test_anything_carrying_an_answer_is_still_released(text):
    """Affirmations are deliberately absent from the pickup set. Roma's greeting ENDS in
    "abhi baat kar sakte hain?", so a lead who says "haan" over its tail has answered it.
    Dropping that would leave Roma waiting for an answer already given and the lead waiting
    for Roma — both silent."""
    g = _guard()
    _send(g, [TranscriptionFrame(text, "lead", "t1"), BotStoppedSpeakingFrame()])
    assert [f.text for f in g.captured if isinstance(f, TranscriptionFrame)] == [text]


def test_a_silent_lead_produces_no_empty_turn():
    g = _guard()
    _send(g, [BotStoppedSpeakingFrame()])
    assert not [f for f in g.captured if isinstance(f, TranscriptionFrame)]
    assert g.opened


def test_the_guard_only_latches_once():
    """A later BotStoppedSpeakingFrame is an ordinary frame, not a second release."""
    g = _guard()
    _send(g, [BotStoppedSpeakingFrame()])
    g.captured.clear()
    _send(g, [TranscriptionFrame("haan", "lead", "t1"), BotStoppedSpeakingFrame()])
    assert [type(f) for f in g.captured] == [TranscriptionFrame, BotStoppedSpeakingFrame]


from roma.realtime.opening import NoiseGate, is_noise_transcript  # noqa: E402


def _gate():
    g = NoiseGate(enable_direct_mode=True)
    g.captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        g.captured.append(frame)

    g.push_frame = _capture
    return g


def test_a_single_character_is_noise():
    assert is_noise_transcript("a")
    assert is_noise_transcript("।")
    assert is_noise_transcript("")
    assert is_noise_transcript("   ")


def test_two_character_affirmations_are_NOT_noise():
    """The threshold sits deliberately below `હા` and `ha`. These decide the P1 inquiry
    confirm — dropping one costs a real answer, which is far worse than answering a cough."""
    assert not is_noise_transcript("હા")
    assert not is_noise_transcript("ha")
    assert not is_noise_transcript("जी")


def test_a_script_roma_never_hears_is_noise():
    """Live call CA3c7d3c7b (2026-07-28). `STT_LANGUAGE = None` gives Saaras auto-detect,
    which is right for a lead who code-switches Hindi/Gujarati mid-clause — but it means a
    breath gets decoded as whatever language fits it best, and on this call that was Tamil.

    The length rule already caught three of them (`ஆ`, one character). `ஆமா` is three, so
    it passed, opened a user turn, and Roma answered it. The lead's very next words were
    "पर मैंने तो कुछ बोला ही नहीं आपको" — "but I didn't say anything to you at all".

    Roma's leads speak Gujarati, Hindi and English. A transcript whose letters are mostly
    none of those three is not something the lead said."""
    assert is_noise_transcript("ஆமா")
    assert is_noise_transcript("ஆஆஆ")
    assert is_noise_transcript("বাংলা")
    assert is_noise_transcript("ಕನ್ನಡ")


def test_the_three_languages_roma_actually_hears_are_never_noise():
    """The guard above must not cost a real answer. These are the scripts every lead-facing
    lexicon already carries, and a false drop here is silence where an answer was."""
    assert not is_noise_transcript("હા જી")
    assert not is_noise_transcript("हाँ अभी बात कर सकते हैं")
    assert not is_noise_transcript("yes I can talk")
    assert not is_noise_transcript("मैंने अभी B.Tech किया है")
    assert not is_noise_transcript("એટલે તને નથી ખબર પડતી")
    assert not is_noise_transcript("मैं 7 बजे आऊंगा।")


def test_punctuation_only_is_noise():
    assert is_noise_transcript("...")
    assert is_noise_transcript("?!")


def test_real_answers_pass():
    for text in ["BCom kiya hai", "વડોદરા", "haan ji", "2024"]:
        assert not is_noise_transcript(text), text


def test_the_gate_drops_noise_and_counts_it():
    g = _gate()
    _send(g, [TranscriptionFrame("a", "lead", "t1")])
    assert g.captured == []
    assert g.dropped == 1


def test_the_gate_passes_a_real_answer_through():
    g = _gate()
    _send(g, [TranscriptionFrame("હા", "lead", "t1")])
    assert [f.text for f in g.captured] == ["હા"]
    assert g.dropped == 0


def test_non_transcription_frames_are_untouched():
    g = _gate()
    _send(g, [BotStoppedSpeakingFrame()])
    assert [type(f) for f in g.captured] == [BotStoppedSpeakingFrame]


def _greeter(**kw):
    from roma.realtime.opening import PickupGreeter

    g = PickupGreeter(enable_direct_mode=True, **kw)
    g.pushed = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        g.pushed.append(frame)

    g.push_frame = capture
    return g


def _runs(g) -> int:
    from pipecat.frames.frames import LLMRunFrame

    return sum(isinstance(f, LLMRunFrame) for f in g.pushed)


def _speech():
    from pipecat.frames.frames import VADUserStartedSpeakingFrame

    return VADUserStartedSpeakingFrame()


def test_the_lead_speaking_starts_the_greeting():
    """THE change. The opening used to be queued at connect, so the lead answered and got
    three to four seconds of silence while it generated — LLM TTFB was 3.28s on the cold
    first turn. Now their "Hello" starts her, the way a phone call actually works."""

    async def run():
        g = _greeter()
        await g.process_frame(_speech(), FrameDirection.DOWNSTREAM)
        return g

    assert _runs(asyncio.run(run())) == 1


def test_a_transcript_also_starts_it():
    """Backstop for a call with no VAD at all, which is how the offline tests run."""

    async def run():
        g = _greeter()
        await g.process_frame(
            TranscriptionFrame("hello", "user", "ts"), FrameDirection.DOWNSTREAM
        )
        return g

    assert _runs(asyncio.run(run())) == 1


def test_the_greeting_fires_exactly_once():
    """Two sounds must not produce two greetings — that is the CA21667bf double-greeting
    this module exists to prevent, re-entering by a different door."""

    async def run():
        g = _greeter()
        for _ in range(3):
            await g.process_frame(_speech(), FrameDirection.DOWNSTREAM)
        return g

    assert _runs(asyncio.run(run())) == 1


def test_silence_still_gets_a_greeting():
    """Plenty of people answer and say nothing. A greeting that waits forever for a sound
    that never comes is a dead call — the fallback is not optional."""

    async def run():
        g = _greeter(max_wait_secs=0.01)
        await g._wait_then_greet()
        return g

    assert _runs(asyncio.run(run())) == 1


def test_the_wait_counts_from_pickup_not_from_pipeline_spin_up():
    """Call 8517d576: a 1.0s wait produced 1.797s of silence at pickup.

    The timer armed on `StartFrame`, which reached this processor 0.789s after the media
    stream opened — so the callee paid for pipeline construction on top of the full wait,
    while the opener bytes had been ready since 0.267s. The wait exists to avoid talking
    over a callee still saying "hello", and that window opens at PICKUP; time already spent
    building the pipeline has already given them their chance.
    """
    import time

    g = _greeter(max_wait_secs=1.0, connected_at=time.monotonic() - 0.8)
    assert 0.0 < g._remaining_wait() <= 0.25, (
        "0.8s of the 1.0s window has already elapsed; only the remainder may be waited"
    )

    spent = _greeter(max_wait_secs=1.0, connected_at=time.monotonic() - 5.0)
    assert spent._remaining_wait() == 0.0, "an already-expired window must not go negative"

    # No `connected_at` (every offline test that builds this directly) is unchanged.
    assert _greeter(max_wait_secs=1.0)._remaining_wait() == 1.0


def test_the_fallback_does_not_double_up_after_the_lead_spoke():
    async def run():
        g = _greeter(max_wait_secs=0.01)
        await g.process_frame(_speech(), FrameDirection.DOWNSTREAM)
        await g._wait_then_greet()
        return g

    assert _runs(asyncio.run(run())) == 1


def test_every_frame_still_passes_through():
    """The greeter observes; it must never withhold. Only `OpeningTurnGuard` holds frames,
    and it sits downstream of this."""
    frame = TranscriptionFrame("kuch bhi", "user", "ts")

    async def run():
        g = _greeter()
        await g.process_frame(frame, FrameDirection.DOWNSTREAM)
        return g

    assert frame in asyncio.run(run()).pushed


# --- inbound: the opener is canned, so the greeter stands down ----------------------------


def test_a_canned_opener_stops_the_greeter_generating_a_second_one():
    """Everything this processor does describes an OUTBOUND call, where Roma dialled and must
    not talk over a callee still saying "hello".

    Inbound she is the one who ANSWERS, so `canned.opening_line()` plays off disk the instant
    the socket connects. Firing `LLMRunFrame` on top of that produces a second unprompted
    line: the caller hears "Hello, Weltec Institute" and then, two seconds later, Roma
    starting a conversation with herself."""

    async def run():
        g = _greeter(opening_already_spoken=True)
        await g.process_frame(_speech(), FrameDirection.DOWNSTREAM)
        await g.process_frame(
            TranscriptionFrame("haan course ke baare mein", "user", "ts"),
            FrameDirection.DOWNSTREAM,
        )
        return g

    g = asyncio.run(run())
    assert _runs(g) == 0, "the canned opener already greeted; this would be the second one"
    assert g.greeted is False


def test_the_canned_opener_does_not_stop_frames_flowing():
    """Standing down means generating nothing, NOT swallowing the pipeline — the caller's
    speech still has to reach STT and the aggregators behind it."""

    async def run():
        g = _greeter(opening_already_spoken=True)
        await g.process_frame(_speech(), FrameDirection.DOWNSTREAM)
        return g

    assert len(asyncio.run(run()).pushed) == 1


def test_without_a_canned_opener_the_greeter_still_works():
    """The degraded path: if the asset is missing, `media.py` logs an error and leaves the
    generated opening turn in place rather than opening the call in silence."""

    async def run():
        g = _greeter(opening_already_spoken=False)
        await g.process_frame(_speech(), FrameDirection.DOWNSTREAM)
        return g

    assert _runs(asyncio.run(run())) == 1


def test_a_canned_opener_leaves_the_guard_open_from_the_start():
    """THE bug that made the first working call unusable (6cb580df, 2026-07-31).

    This gate opens on `BotStoppedSpeakingFrame`, which the TTS service emits when a
    GENERATED utterance ends. The canned opener is queued at connect as raw audio, so that
    frame never arrives — the gate stayed shut for the entire call and every transcript was
    held and never released. Roma said "Hello, Weltec Institute" and then went silent while
    the lead said "कुछ तो बोलिए आगे" ("say something, then") into a line that had already
    stopped listening.

    Same shape as the dead watchdog disarm: a guard keyed on a frame the architecture
    stopped producing."""

    async def run():
        g = _guard(opener_is_raw_audio=True)
        await g.process_frame(
            TranscriptionFrame("course ke baare mein poochhna tha", "user", "ts"),
            FrameDirection.DOWNSTREAM,
        )
        return g

    g = asyncio.run(run())
    assert g.opened is True
    assert g.held == [], "a canned opener has nothing to wait for; nothing may be held"
    assert any(isinstance(f, TranscriptionFrame) for f in g.captured), (
        "the lead's words must reach the aggregator, not be swallowed"
    )


def test_without_a_canned_opener_the_guard_still_holds():
    """The generated-opening path is unchanged: hold until she has finished speaking."""

    async def run():
        g = _guard(opener_is_raw_audio=False)
        await g.process_frame(
            TranscriptionFrame("haan bolo", "user", "ts"), FrameDirection.DOWNSTREAM
        )
        return g

    g = asyncio.run(run())
    assert g.opened is False
    assert g.held == ["haan bolo"]


def test_the_outbound_greeter_speaks_the_opener_instead_of_asking_the_llm_for_it():
    """Live calls acf8e78f and c5672a23 (2026-08-01) were SILENT end to end: the lead
    answered, heard nothing, and hung up. Both times the FIRST completion stalled — 64s and
    18.6s — on a link that otherwise carried the call.

    `p1_open.md` orders one exact sentence, so asking for it spends a network round trip to
    be told what we already know, on the one turn where silence costs most.

    The trio matters, not just the text: `PreTTSFilterProcessor` keys `_first_of_turn` on the
    start frame, and the assistant aggregator needs it to record the greeting into context —
    without which Roma's own opener is missing from the history she reasons over."""
    import asyncio

    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMRunFrame,
        TextFrame,
    )
    from roma.realtime.opening import PickupGreeter

    pushed = []
    g = PickupGreeter(opening_text_fn=lambda: "Hello Nihit ji, main Roma baat kar rahi hoon.")
    g.push_frame = lambda f, d=None: pushed.append(f) or asyncio.sleep(0)
    asyncio.run(g._greet("test"))

    kinds = [type(f) for f in pushed]
    assert LLMRunFrame not in kinds, "still asking the LLM for a line it was handed verbatim"
    assert kinds == [LLMFullResponseStartFrame, TextFrame, LLMFullResponseEndFrame]
    assert pushed[1].text.startswith("Hello Nihit ji,")


def test_the_greeter_falls_back_to_generating_when_no_opener_is_supplied():
    """Inbound supplies no text (its opener is the canned .ulaw), and a render that fails
    must not cost the greeting. A slow greeting beats no greeting."""
    import asyncio

    from pipecat.frames.frames import LLMRunFrame
    from roma.realtime.opening import PickupGreeter

    def collect_into(sink):
        return lambda f, d=None: sink.append(f) or asyncio.sleep(0)

    for fn in (None, lambda: "", lambda: 1 / 0):
        pushed = []
        g = PickupGreeter(opening_text_fn=fn)
        g.push_frame = collect_into(pushed)
        asyncio.run(g._greet("test"))
        assert [type(f) for f in pushed] == [LLMRunFrame], f"no fallback for {fn!r}"
