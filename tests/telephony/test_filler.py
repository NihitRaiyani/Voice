"""The filler token (docs/05 "Hiding the 850ms", docs/02, docs/06).

Measured on live call CA9c5f7cf: OpenAI TTFB 1.21s, Bulbul 0.46s, against an 850ms endpoint
wait. The LLM alone costs more than the knob docs/10 Step 7 set out to tune, so the budget
closes by HIDING latency rather than eliminating it — which is what docs/02 says.
"""

import asyncio

from pipecat.frames.frames import LLMContextFrame, OutputAudioRawFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from roma.controller.state import CallState
from roma.telephony import filler as filler_mod
from roma.telephony import phase_controller as pc_mod
from roma.telephony.filler import FILLER_LINES, FillerClip, FillerPicker, load_fillers
from roma.telephony.phase_controller import PhaseControllerProcessor

VARS = {"branch": "Vadodara", "lead_name": "ji"}


def _clip(name="achha", ms=250):
    return FillerClip(name=name, text="Achha…", pcm=b"\x00\x01" * (8 * ms))


def test_the_rendered_clips_exist_and_are_short_enough():
    """A filler sits IN FRONT of Roma's real sentence, so its own length is added to the
    latency it exists to hide. docs/05 says 200-300ms; Bulbul pads short utterances, which
    is why `make_filler_clips.py` trims silence. Half a second is the outer limit before
    the mask costs more than it saves — except the OBJECTION bucket, whose looser cap is a
    stated trade in `OBJECTION_LINES`: those turns have the slowest completions (the P6
    reframe), and empathy clipped short reads as dismissal."""
    from roma.telephony.filler import OBJECTION_LINES

    clips = load_fillers()
    assert clips, "no filler assets — run scripts/make_filler_clips.py"
    for c in clips:
        limit = 0.9 if c.name in OBJECTION_LINES else 0.55
        assert 0.1 < c.secs <= limit, f"{c.name} is {c.secs:.2f}s (limit {limit}s)"


def test_every_declared_line_was_rendered():
    from roma.telephony.filler import OBJECTION_LINES, QUESTION_LINES

    declared = set(FILLER_LINES) | set(QUESTION_LINES) | set(OBJECTION_LINES)
    assert {c.name for c in load_fillers()} == declared


def test_the_filler_lines_pass_the_pre_TTS_filter():
    """Audio reaching the wire has passed docs/04, with no exemption for being short."""
    from roma.guardrails import safe_output

    for text in FILLER_LINES.values():
        assert safe_output(text) == text


def test_picker_rotates_rather_than_repeating():
    """'achha… achha… achha…' over a six-turn call is a worse artefact than the silence it
    replaced, which is why this rotates instead of choosing at random."""
    p = FillerPicker([_clip("a"), _clip("b"), _clip("c")])
    assert [p.next().name for _ in range(4)] == ["a", "b", "c", "a"]


def test_picker_with_no_clips_is_falsy_and_yields_none():
    p = FillerPicker([])
    assert not p
    assert p.next() is None


def test_picker_counts_confirmed_plays_not_attempts():
    """`played` used to increment inside `next()`, so a push that failed still bumped it
    and the teardown line could claim a mask the lead never heard. The caller now reports
    success via `note_played()` after the frame actually went downstream."""
    p = FillerPicker([_clip()])
    p.next()
    p.next()
    assert p.played == 0, "handing out a clip is not playing it"
    p.note_played()
    p.note_played()
    assert p.played == 2


def test_intent_buckets_route_and_fall_back_to_neutral():
    from roma.telephony.filler import FillerIntent

    dekhiye = _clip("dekhiye")
    empathy = _clip("samajh_rahi_hoon")
    neutral = _clip("achha")
    p = FillerPicker([neutral, dekhiye, empathy])
    assert p.next(FillerIntent.QUESTION).name == "dekhiye"
    assert p.next(FillerIntent.OBJECTION).name == "samajh_rahi_hoon"
    assert p.next().name == "achha"

    # A pool with no intent clips (today's shipped assets, until the render runs) must
    # fall back to the neutral rotation rather than silence.
    p2 = FillerPicker([neutral])
    assert p2.next(FillerIntent.QUESTION).name == "achha"
    assert p2.next(FillerIntent.OBJECTION).name == "achha"


def test_intent_for_matches_the_turn_shape():
    from roma.telephony.filler import FillerIntent, intent_for

    assert intent_for("ye to bahut mehenga hai") is FillerIntent.OBJECTION
    assert intent_for("course kitne mahine ka hai") is FillerIntent.QUESTION
    assert intent_for("Batch kab hoti hai?") is FillerIntent.QUESTION
    assert intent_for("BCom kiya hai maine") is FillerIntent.NEUTRAL
    assert intent_for("") is FillerIntent.NEUTRAL
    assert intent_for(None) is FillerIntent.NEUTRAL


def test_the_phase_outranks_the_text_classifier():
    """Both live mismatches from call 8517d576 (2026-08-08).

    The text classifier is a GUESS at the same question the machine has already answered,
    so where the machine has an opinion it wins. `dekhiye (question, p6_objection)` opened
    an objection turn like a lecture; `dekhiye (question, p1_open)` opened the lead's very
    first words the same way.
    """
    from roma.telephony.filler import FillerIntent, intent_for

    assert intent_for("ye kitna mehenga hai", "p6_objection") is FillerIntent.OBJECTION
    assert intent_for("kya batayenge", "p6_objection") is FillerIntent.OBJECTION
    for text in ("haan kya hai", "ji kaun bol raha hai", "mehenga hai"):
        assert intent_for(text, "p1_open") is FillerIntent.NEUTRAL, text
    # Where the machine has no opinion, the text still decides.
    assert intent_for("course kitne mahine ka hai", "p2_discover") is FillerIntent.QUESTION
    assert intent_for("bahut mehenga hai", "p5_pivot") is FillerIntent.OBJECTION


def test_each_intent_bucket_rotates_rather_than_repeating():
    """A bucket of one is a cycle of one. Turns 10 and 12 of call 8517d576 were both
    objections and both got "Samajh rahi hoon…" fourteen seconds apart — the anti-tic
    alternation only blocks CONSECUTIVE turns, so the bucket itself has to rotate."""
    from roma.telephony.filler import FILLER_LINES, OBJECTION_LINES, QUESTION_LINES

    for bank in (QUESTION_LINES, OBJECTION_LINES, FILLER_LINES):
        assert len(bank) >= 2, f"a bucket of one repeats on its second use: {bank}"


def test_the_intent_lines_pass_the_pre_TTS_filter_and_hold_register():
    from roma.guardrails import safe_output
    from roma.telephony.filler import OBJECTION_LINES, QUESTION_LINES

    for text in {**QUESTION_LINES, **OBJECTION_LINES}.values():
        assert safe_output(text) == text
        assert "raha hoon" not in text.casefold(), "female self-forms only"


def test_load_fillers_survives_a_missing_assets_dir(monkeypatch, tmp_path):
    """A missing filler is a latency regression, not a reason to refuse the call — unlike
    the consent line, where a missing asset MUST be fatal."""
    monkeypatch.setattr(filler_mod, "_ASSETS", tmp_path)
    assert load_fillers() == []


def _drive(proc, frames):
    captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    proc.push_frame = _capture

    async def run():
        for f in frames:
            await proc.process_frame(f, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return captured


def _proc(fillers):
    return PhaseControllerProcessor(
        CallState(call_sid="t", **VARS),
        client=None,
        now_fn=lambda: __import__("datetime").datetime(2026, 7, 25, 10, 0),
        fillers=fillers,
    )


def test_a_user_turn_emits_one_filler_before_the_llm_sees_anything():
    proc = _proc(FillerPicker([_clip()]))
    ctx = LLMContext(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "haan"}]
    )
    out = _drive(proc, [LLMContextFrame(ctx)])
    audio = [f for f in out if isinstance(f, OutputAudioRawFrame)]
    assert len(audio) == 1
    assert out.index(audio[0]) < out.index(
        next(f for f in out if isinstance(f, LLMContextFrame))
    )


def test_the_opening_turn_gets_no_filler():
    """Nothing has been asked yet — an 'achha…' before Roma's own greeting is nonsense."""
    proc = _proc(FillerPicker([_clip()]))
    ctx = LLMContext(messages=[{"role": "system", "content": "s"}])
    out = _drive(proc, [LLMContextFrame(ctx)])
    assert not [f for f in out if isinstance(f, OutputAudioRawFrame)]


def _user_ctx(text="haan"):
    return LLMContextFrame(
        LLMContext(
            messages=[{"role": "system", "content": "s"}, {"role": "user", "content": text}]
        )
    )


def _audio(out):
    return [f for f in out if isinstance(f, OutputAudioRawFrame)]


def test_the_filler_does_not_play_on_consecutive_turns():
    """Live call CA3c7d3c7b (2026-07-28): "repeatedly accha ji is coming which is annoying".

    `enable_filler` was turned on for the first time for that call, so every one of Roma's
    fourteen turns was preceded by a clip drawn from a pool of three. Rotation was working
    exactly as designed and that is the problem — a token every single turn IS the tic, no
    matter how many tokens there are.

    So the clip now covers at most every other turn. The gap it hides is real, but a lead
    who hears an acknowledgement before every sentence stops hearing a person."""
    proc = _proc(FillerPicker([_clip(), _clip()]))
    assert len(_audio(_drive(proc, [_user_ctx("pehla")]))) == 1
    assert len(_audio(_drive(proc, [_user_ctx("doosra")]))) == 0, "played twice running"
    assert len(_audio(_drive(proc, [_user_ctx("teesra")]))) == 1, "should resume after a skip"


def test_the_closing_phase_gets_no_filler():
    """P7 turns are capped at twenty-five words — the clip is a meaningful fraction of the
    line it precedes, and "achha… Theek hai, milte hain" is two acknowledgements and a
    goodbye. The mask costs more than the gap here."""
    from roma.controller.machine import P7_CLOSE

    proc = _proc(FillerPicker([_clip(), _clip()]))
    proc.state.phase = P7_CLOSE
    assert not _audio(_drive(proc, [_user_ctx("haan theek hai")]))


def test_the_filler_vocabulary_is_neutral_and_not_all_achha():
    """Two rules the pool has to satisfy, both learned on CA3c7d3c7b.

    It must be wide enough that half-rate play does not still sound like one word — three
    was not. And every token must be NEUTRAL: the clip is spoken before the model has
    decided anything, so "Haan ji…" (agreement) was wrong on any turn where Roma then
    disagreed, and it is half of what the lead heard as "accha ji"."""
    assert len(FILLER_LINES) >= 4, "pool too small to disguise repetition"
    assert "haan_ji" not in FILLER_LINES, "agreement is a stance; the filler precedes one"
    lowered = " ".join(FILLER_LINES.values()).casefold()
    assert "haan ji" not in lowered


def test_no_clips_means_no_audio_and_no_crash():
    proc = _proc(FillerPicker([]))
    ctx = LLMContext(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "haan"}]
    )
    out = _drive(proc, [LLMContextFrame(ctx)])
    assert not [f for f in out if isinstance(f, OutputAudioRawFrame)]
    assert any(isinstance(f, LLMContextFrame) for f in out)


def test_fillers_none_is_tolerated():
    """`fillers=None` is the default — every existing construction site passes nothing."""
    proc = _proc(None)
    ctx = LLMContext(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "haan"}]
    )
    out = _drive(proc, [LLMContextFrame(ctx)])
    assert any(isinstance(f, LLMContextFrame) for f in out)


def test_the_filler_carries_the_telephony_sample_rate():
    """8kHz mono, or Twilio plays it at the wrong speed."""
    proc = _proc(FillerPicker([_clip()]))
    ctx = LLMContext(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "haan"}]
    )
    out = _drive(proc, [LLMContextFrame(ctx)])
    audio = next(f for f in out if isinstance(f, OutputAudioRawFrame))
    assert audio.sample_rate == 8000
    assert audio.num_channels == 1


def test_the_teardown_line_reports_what_the_filler_actually_did():
    """The counter existed and was never printed, and that cost a diagnosis.

    `_emit_filler` logs only on failure, so after CA3c7d3c7b a grep for filler activity
    returned nothing — which reads as "it never played" when it had in fact played on all
    fourteen turns and WAS the lead's complaint. Same class as the dead VAD that survived
    four calls because teardown printed configuration instead of evidence.

    `getattr(..., "played", "off")` so a call with the feature disabled says "off" rather
    than "0" — "never ran" and "ran and did nothing" are different answers."""
    import inspect

    from roma.telephony import media

    src = inspect.getsource(media)
    assert "fillers_played=%s" in src
    assert 'getattr(phase_ctrl._fillers, "played", "off")' in src


def test_every_clip_is_levelled_to_roma_s_own_speaking_voice():
    """Live call CA3cc181ed (2026-07-28): "every voice tone is different, don't have any
    consistent voice".

    Measured from that call's recording, Roma's live TTS sits at RMS ~4221. The shipped
    clips sat at 4964, 5923, 6799 and 7836 — every one LOUDER than her, up to 1.86x
    (+5.4dB) for "ji". With the clip now covering only every other turn, the lead heard a
    loud token, then her quieter voice, then a turn with no token at all. An intermittent
    level jump is heard as an inconsistent voice, which is exactly what was reported.

    The two worst were the two rendered on 2026-07-28, in a different session from the
    originals — so this is not a one-off but a reproducible property of rendering clips
    piecemeal. Levelling is therefore pinned here rather than left to whoever renders next.
    """
    import numpy as np

    from roma.telephony.filler import FILLER_TARGET_RMS

    for clip in load_fillers():
        pcm = np.frombuffer(clip.pcm, dtype="<i2").astype(np.float32)
        voiced = pcm[np.abs(pcm) > 200]
        assert len(voiced), f"{clip.name} is silence"
        rms = float(np.sqrt((voiced**2).mean()))
        assert 0.8 * FILLER_TARGET_RMS <= rms <= 1.25 * FILLER_TARGET_RMS, (
            f"{clip.name} at RMS {rms:.0f} against a target of {FILLER_TARGET_RMS}"
        )
        assert np.abs(pcm).max() < 32000, f"{clip.name} clips"


def _slow_proc(fillers, delay, phase="p2_discover"):
    """A controller whose slot extraction takes `delay` seconds — the live shape, where the
    round trip measured 1.93s p50 while the clip covering it is 0.37s long."""

    async def _slow_extract(client, text, slot_name):
        await asyncio.sleep(delay)
        from roma.controller.slots import DiscoveryValue

        return DiscoveryValue()

    state = CallState(call_sid="t", **VARS)
    state.phase = phase
    return PhaseControllerProcessor(
        state,
        client=object(),
        now_fn=lambda: __import__("datetime").datetime(2026, 7, 25, 10, 0),
        fillers=fillers,
        holding=FillerPicker([_clip(), _clip()]),
        extract_discovery=_slow_extract,
    )


def test_a_fast_turn_gets_no_extra_clip():
    """A turn that finishes inside the pace window hears exactly what it always did. The
    extra clip is earned by being slow, not given to every turn — that was the tic."""
    proc = _slow_proc(FillerPicker([_clip(), _clip()]), delay=0.0)
    out = _drive(proc, [_user_ctx()])
    assert len(_audio(out)) == 1
    assert proc.pacer_clips == 0


def test_an_ordinary_turn_is_never_narrated(monkeypatch):
    """Live call 1cd20b57: 13 held clips across 7 turns, because the hold fired at
    FILLER_PACE_SECS (1.1s) while a NORMAL turn takes ~4.2s end to end. Every ordinary pause
    got narrated. The lead: "1 second जी 1 second जी लगे रखा है बार बार आप repeat कर रहे हो,
    what is this manner" — the "accha accha" tic rebuilt by hand.

    The hold is a STALL signal (stalls measured 18-64s), not a pause filler."""
    assert pc_mod.HOLD_AFTER_SECS >= 9.0, "an ordinary turn would trip the hold again"
    proc = _slow_proc(FillerPicker([_clip(), _clip()]), delay=0.3)
    _drive(proc, [_user_ctx()])
    assert proc.pacer_clips == 0, "a normal-length turn was narrated"
    asyncio.run(proc._stop_pacer())


def test_the_line_is_never_held_mid_conversation(monkeypatch):
    """REMOVED 2026-08-02 at the lead's third complaint: "Just remove this bro. What is this?
    एक second, एक minute रुकिए." And, damningly: "आप जो बोल रहे थे वो ठीक ही बोल रहे थे सही
    flow में जा रहे थे, 1 second आपने क्यों बोला" — it broke a conversation that was working.

    It was built to cover 57s and 28s stalls and it did, but it was re-tuned three times
    (1.1s -> 6s -> 9s) and still fired 4 times in 6 turns, because ordinary turns on this link
    run past 9s. A cover that fires on ordinary turns is a tic, not a cover — the same lesson
    `_should_fill` learned from "accha accha".

    Both real stall causes are fixed at the source now (LLM-free opener, capped-and-retried
    completion). The machinery is kept for a worse link; this pins that it stays off."""
    monkeypatch.setattr(pc_mod, "HOLD_AFTER_SECS", 0.01)
    monkeypatch.setattr(pc_mod, "HOLD_REPEAT_SECS", 0.01)
    proc = _slow_proc(FillerPicker([_clip(), _clip()]), delay=0.3)
    _drive(proc, [_user_ctx()])
    assert proc.pacer_clips == 0, "the line was held mid-conversation again"
    assert proc._pacer is None


def test_the_ack_forms_roma_actually_writes_are_stripped():
    """Live regression, call 98aa06a3 (2026-08-04): the clip and the line said the same thing.

    `_LEADING_ACKS` already contained "samajh gayi", but matching is `startswith` and the
    model does not write the bare phrase — it writes it WITH the pronoun:

        13:00:17  "Koi baat nahi, main samajh gayi. Toh aap mujhe bata sakte hain..."
        13:00:38  "Main samajh gayi. Aap abhi padh rahe hain ya job kar rahe hain?"

    So the `samjhi` clip played and Roma then said it again in words, on two consecutive
    turns. That is the "repeatedly accha ji is coming which is annoying me" complaint
    rebuilt out of a different word — a vocabulary that lists the phrase but not the form
    is not coverage.
    """
    from roma.telephony.filler import strip_leading_ack

    for line, must_go in (
        ("Main samajh gayi. Aap abhi padh rahe hain?", "samajh gayi"),
        ("Koi baat nahi, main samajh gayi. Toh aap bataiye.", "samajh gayi"),
        ("Main samjhi. Aage bataiye.", "samjhi"),
    ):
        out = strip_leading_ack(line)
        assert must_go not in out.casefold(), (
            f"{line!r} still opens with an acknowledgement the filler clip just played"
        )
        assert out.strip(), "stripping must never leave the turn empty"
