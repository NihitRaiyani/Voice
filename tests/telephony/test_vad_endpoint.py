"""What `stop_secs` actually costs, in frames (docs/05 Layer 1, docs/10 Step 7).

`stop_secs` is dead air added to EVERY turn: the lead stops talking and nothing downstream
moves until the hangover expires. Measured on live call 932b6c88 the turn budget was ~3.6s
from the lead's last word to Roma's first audio, and this knob is the first ~750ms of it,
ahead of STT (~1.1s), the LLM (~1.2s) and TTS (~0.55s).

## What this file can and cannot prove

It CANNOT prove "0.45s does not cause false endpoints on code-mix speech". That depends on
Silero's speech/non-speech judgement over real Gujarati/Hindi/English audio and on how long
real leads actually pause mid-sentence — both live measurements. A unit test asserting it
would be theatre.

What it CAN prove, exactly, is the hangover boundary, because pipecat derives it in FRAMES,
not wall-clock (`vad_analyzer.py:160-163`):

    vad_stop_frames = round(stop_secs / (num_frames_required / sample_rate))

At 8kHz with 256-sample frames that is 32ms per frame, so the boundary is deterministic and
these tests pin it to the frame. The `_EnergyVAD` stub (borrowed from `test_vad_stage.py`)
replaces ONLY `voice_confidence` — the real hangover state machine runs.

## The number the tuning decision needs

Lowering 0.75 -> 0.45 returns ~288ms per turn and moves the endpoint boundary from 736ms to
448ms. **Any mid-sentence pause between those two figures, which used to be ridden out, now
ends the turn.** That window is the entire risk of the change, and
`test_the_risk_window_opened_by_the_retune_is_this_wide` states it as a measured span rather
than a worry. Whether real code-mix pauses fall inside it is the live call's job to answer;
`Settings.vad_stop_secs` makes the revert one env var.
"""

import asyncio

from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams, VADState

from roma.config import Settings
from roma.telephony.backchannel import vad_span_secs
from roma.telephony.media import VAD_STOP_SECS, build_vad

RATE = 8000
CHUNK = 256
SECS_PER_FRAME = CHUNK / RATE  # 32ms

# The retune under test. `PREVIOUS` is kept so the saving is asserted, not asserted-about.
TUNED_STOP_SECS = 0.45
PREVIOUS_STOP_SECS = 0.75


class _EnergyVAD(VADAnalyzer):
    """Speech = loud. Substitutes for Silero's neural net and NOTHING else.

    Same stub as `test_vad_stage.py:76-94`, duplicated rather than imported because these
    two files test different things about the same machinery and a shared helper across
    test modules is the kind of coupling that makes one break the other.
    """

    def num_frames_required(self) -> int:
        return CHUNK

    def voice_confidence(self, buffer) -> float:
        if not buffer:
            return 0.0
        peak = max(
            abs(int.from_bytes(buffer[i : i + 2], "little", signed=True))
            for i in range(0, len(buffer) - 1, 2)
        )
        return 1.0 if peak > 8000 else 0.0


def _analyzer(stop_secs: float) -> _EnergyVAD:
    """A started analyzer.

    `set_sample_rate` is what derives `_vad_stop_frames` from `stop_secs` (`set_params`,
    `vad_analyzer.py:149-166`); the constructor alone leaves them unset. In the pipeline the
    transport makes this call at `StartFrame`, so an analyzer that has never been started is
    not the object the call uses.
    """
    vad = _EnergyVAD(sample_rate=RATE, params=VADParams(stop_secs=stop_secs, confidence=0.5))
    vad.set_sample_rate(RATE)
    return vad


def _chunk(amplitude: int) -> bytes:
    return int(amplitude).to_bytes(2, "little", signed=True) * CHUNK


def _frames_for(secs: float) -> int:
    """How many 32ms analyzer frames a pause of `secs` occupies."""
    return round(secs / SECS_PER_FRAME)


def _speak_then_pause(stop_secs: float, pause_secs: float) -> VADState:
    """Talk until SPEAKING is reached, go quiet for `pause_secs`, return the state.

    QUIET means the turn ended — the lead's pause was read as "they are finished". Anything
    else means the hangover rode the pause out and the turn is still open.
    """
    vad = _analyzer(stop_secs)

    async def run():
        state = None
        for _ in range(_frames_for(1.0)):  # comfortably past start_secs
            state = await vad.analyze_audio(_chunk(20000))
        assert state == VADState.SPEAKING, "the stub never reached SPEAKING; test is invalid"
        for _ in range(_frames_for(pause_secs)):
            state = await vad.analyze_audio(_chunk(0))
        return state

    return asyncio.run(run())


def test_the_hangover_is_derived_in_frames_not_wall_clock():
    """The premise everything below rests on. If pipecat ever switches to a timer, these
    become flaky rather than wrong, and this test says so first."""
    vad = _analyzer(TUNED_STOP_SECS)
    assert vad._vad_stop_frames == _frames_for(TUNED_STOP_SECS)
    assert vad._vad_stop_frames * SECS_PER_FRAME == 0.448


def test_a_short_mid_sentence_pause_does_not_end_the_turn_at_the_tuned_value():
    """300ms — a breath between clauses. Must survive the retune."""
    assert _speak_then_pause(TUNED_STOP_SECS, 0.30) != VADState.QUIET


def test_a_real_stop_still_ends_the_turn_at_the_tuned_value():
    """800ms of silence is someone who has finished. Must still endpoint."""
    assert _speak_then_pause(TUNED_STOP_SECS, 0.80) == VADState.QUIET


def test_the_retune_returns_this_many_milliseconds_per_turn():
    """The saving, as a number rather than a claim."""
    tuned = _analyzer(TUNED_STOP_SECS)._vad_stop_frames * SECS_PER_FRAME
    previous = _analyzer(PREVIOUS_STOP_SECS)._vad_stop_frames * SECS_PER_FRAME
    saved_ms = (previous - tuned) * 1000
    assert 250 <= saved_ms <= 320, f"expected ~288ms back per turn, measured {saved_ms:.0f}ms"


def test_the_risk_window_opened_by_the_retune_is_this_wide():
    """The cost, stated as precisely as the saving.

    A pause of 600ms rode out at 0.75 and endpoints at 0.45. Every pause in the 448-736ms
    band changes behaviour; nothing outside it does. If a live call shows Roma cutting leads
    off mid-sentence, this band is where to look — and raising `vad_stop_secs` back is the
    fix, not a code change.
    """
    assert _speak_then_pause(PREVIOUS_STOP_SECS, 0.60) != VADState.QUIET
    assert _speak_then_pause(TUNED_STOP_SECS, 0.60) == VADState.QUIET
    # Outside the band, both settings agree — the change is bounded.
    assert _speak_then_pause(PREVIOUS_STOP_SECS, 0.30) == _speak_then_pause(
        TUNED_STOP_SECS, 0.30
    )
    assert _speak_then_pause(PREVIOUS_STOP_SECS, 0.90) == _speak_then_pause(
        TUNED_STOP_SECS, 0.90
    )


def test_the_backchannel_span_math_survives_the_retune():
    """`vad_span_secs` must recover the TRUE acoustic span at any `stop_secs`.

    It reads `stop_secs` off the frame rather than a constant, which is what makes it
    self-correcting — but only if the value on the frame matches the analyzer that emitted
    it. A hard-coded 0.85 in that formula would silently inflate every span by 400ms after
    this retune, push every utterance past `BACKCHANNEL_MAX_SECS`, and disable backchannel
    suppression with no error and no failing test (`backchannel.py:284-300`).
    """

    class _F:
        def __init__(self, timestamp, start_secs=0.0, stop_secs=0.0):
            self.timestamp = timestamp
            self.start_secs = start_secs
            self.stop_secs = stop_secs

    for stop_secs in (PREVIOUS_STOP_SECS, TUNED_STOP_SECS):
        # A lead spoke for exactly 500ms. Frames land start_secs/stop_secs after the fact.
        start = _F(timestamp=10.0 + 0.2, start_secs=0.2)
        stop = _F(timestamp=10.5 + stop_secs, stop_secs=stop_secs)
        span = vad_span_secs(start, stop)
        assert abs(span - 0.5) < 1e-6, (
            f"at stop_secs={stop_secs} the recovered span was {span}, not the 0.5s actually "
            "spoken — backchannel suppression reads this number"
        )


def test_the_shipped_default_is_the_tuned_value():
    """Settings and the module constant must not drift apart: `build_vad` reads Settings,
    but `media.py` also exports `VAD_STOP_SECS`, and the turn strategies were built against
    it. Two sources of truth for one knob is how a retune half-lands."""
    assert Settings().vad_stop_secs == TUNED_STOP_SECS
    assert VAD_STOP_SECS == TUNED_STOP_SECS
    assert build_vad(Settings()).params.stop_secs == TUNED_STOP_SECS
