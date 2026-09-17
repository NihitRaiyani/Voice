"""The shared answer to "is a reply on its way?" (roma.telephony.turnflight).

Live call CA4ba2a6b8 (2026-07-28) went dead 65 seconds in. The watchdog nudged 4.9s after a
finalized transcript, a second generation started on top of the first, and the TTS context
was torn down mid-stream — every audio frame after that was discarded for having no context.

The disarm that should have prevented it was DEAD CODE: `LLMUserAggregator` consumes
`TranscriptionFrame` and never pushes it downstream, so the watchdog, which sits past the
output transport, never saw one. These tests exist because the previous fix was correct in
isolation and never ran.
"""

import asyncio
import time

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from roma.controller.state import CallState
from roma.telephony.phase_controller import PhaseControllerProcessor
from roma.telephony.turnflight import TurnFlight

VARS = {"branch": "Vadodara", "lead_name": "ji"}


def test_a_fresh_flight_owes_nothing():
    assert TurnFlight().pending is False
    assert TurnFlight().summary() == {}


def test_a_started_turn_is_pending_until_audio():
    f = TurnFlight()
    f.turn_started()
    assert f.pending is True
    f.first_audio()
    assert f.pending is False


def test_only_the_first_audio_frame_of_a_turn_is_timed():
    """A reply is many audio frames. Timing each would report ~0 for every frame after the
    first and drag the median to nothing."""
    f = TurnFlight()
    f.turn_started()
    for _ in range(5):
        f.first_audio()
    assert f.summary()["n"] == 1


def test_a_turn_that_never_speaks_expires():
    """The safety valve: without it, one dead generation silences the watchdog for the rest
    of the call — and rescuing exactly that case is the watchdog's whole job."""
    f = TurnFlight(max_pending_secs=0.05)
    f.turn_started()
    time.sleep(0.06)
    assert f.pending is False
    assert f.summary() == {}, "an expired turn is lost, not a latency sample"


def test_summary_reports_p50_and_max_but_never_a_fake_p95():
    """A per-call p95 is the maximum wearing a percentile's name — nearest-rank returns the
    last index until ~40 samples and a call has 15-25 turns."""
    f = TurnFlight()
    f.latencies = [1.0, 2.0, 3.0, 10.0]
    s = f.summary()
    assert s["n"] == 4 and s["max"] == 10.0
    assert s["p50"] == 3.0
    assert "p95" not in s


def test_the_heard_span_is_measured_separately_and_is_larger():
    """`latencies` starts at the CONTROLLER, which is after endpointing and the aggregator.
    Measured against CA00417672's log the lead's real gap was 3.12s p50 against the 2.19s
    reported — anything tuned against the smaller number is tuned against ~70% of the gap."""
    f = TurnFlight()
    f.user_stopped()
    time.sleep(0.03)
    f.turn_started()
    time.sleep(0.03)
    f.first_audio()
    assert f.heard_summary()["n"] == 1
    assert f.heard_summary()["p50"] > f.summary()["p50"]


def test_a_turn_with_no_transcript_still_times_the_controller_span():
    """The greeting has no transcript before it. It must not silently skew the heard span."""
    f = TurnFlight()
    f.turn_started()
    f.first_audio()
    assert f.summary()["n"] == 1
    assert f.heard_summary() == {}


def _drive(proc, frames):
    async def _noop(frame, direction=FrameDirection.DOWNSTREAM):
        return None

    proc.push_frame = _noop

    async def run():
        for f in frames:
            await proc.process_frame(f, FrameDirection.DOWNSTREAM)

    asyncio.run(run())


def _ctx(*, user):
    msgs = [{"role": "system", "content": "s"}]
    if user:
        msgs.append({"role": "user", "content": "haan ji"})
    return LLMContextFrame(LLMContext(messages=msgs))


def _proc(flight):
    return PhaseControllerProcessor(
        CallState(call_sid="t", **VARS),
        client=None,
        now_fn=lambda: __import__("datetime").datetime(2026, 7, 25, 10, 0),
        flight=flight,
    )


def test_the_phase_controller_declares_a_real_user_turn():
    """It is the only processor that sees a turn BEGIN — the transcript never reaches the end
    of the pipeline, so nothing downstream can work this out for itself."""
    f = TurnFlight()
    _drive(_proc(f), [_ctx(user=True)])
    assert f.pending is True


def test_the_opening_turn_is_pending_too():
    """Inverted deliberately. The opening carries no user text and does no slot extraction,
    but it is still a generation in flight, and `SilenceWatchdog` arms on ANY
    `InterruptionFrame` — a lead who says "hello" over the greeting can arm it while the
    greeting is still being produced. Excluding the opening left that window open for
    nothing, and an overlapping generation is what killed CA4ba2a6b8."""
    f = TurnFlight()
    _drive(_proc(f), [_ctx(user=False)])
    assert f.pending is True


def test_the_watchdog_and_the_controller_share_one_flight():
    """The bug class this whole module exists for: code that is correct and never invoked.
    If these two are ever handed different objects, the gate silently stops working and the
    call goes dead again — with every unit test still green."""
    import inspect

    from roma.telephony import media

    src = inspect.getsource(media.build_media_app)
    assert "flight = TurnFlight()" in src
    assert "SilenceWatchdog(flight=flight)" in src
    assert "flight=flight," in src, "the phase controller must get the same object"


def test_the_slot_client_is_shared_across_calls_and_bounded():
    """A1 + A2. Two separate failures pinned together because both are invisible at runtime.

    SHARED: `build_slot_client` used to run per websocket, so every call got a fresh httpx
    pool and therefore a guaranteed cold TLS handshake on its first extraction — measured
    4058ms cold against 1932ms warm.

    BOUNDED: the client carried no timeout, so the SDK defaults applied — `read=600s` with
    `max_retries=2`, a **thirty-minute** worst case on the live critical path, because
    `advance_turn` awaits this before the conversation LLM is asked."""
    import inspect

    from roma.telephony import media

    src = inspect.getsource(media.build_media_app)
    assert "app.state.slot_client = build_slot_client_fn(settings)" in src
    assert "slot_client = app.state.slot_client" in src, "per-call construction is back"

    built = media.build_slot_client(_settings_stub())
    assert built.timeout.read == media.SLOT_READ_TIMEOUT_SECS
    assert built.timeout.connect == media.SLOT_CONNECT_TIMEOUT_SECS
    assert built.max_retries == 1, "the SDK default of 2 doubles the worst case"


def test_the_prelude_read_is_bounded():
    """C3. The message COUNT was capped but each `receive_text()` was not, so a peer that
    connected and said nothing held a websocket, a task and a Silero session indefinitely."""
    import inspect

    from roma.telephony import media

    src = inspect.getsource(media._read_start)
    assert "asyncio.wait_for" in src
    assert media._PRELUDE_TIMEOUT_SECS > 0


def _settings_stub():
    class _S:
        class _K:
            @staticmethod
            def get_secret_value():
                return "sk-test"

        openai_api_key = _K()

    return _S()


def test_a_re_driven_utterance_does_not_advance_the_machine_twice():
    """A `SilenceWatchdog` nudge pushes an `LLMRunFrame`, the aggregator re-emits the SAME
    context, and the machine used to run again on words the lead said once: a second
    slot-extraction round trip on the critical path, a second turn_count increment, and a
    second phase-advance decision for one signal. The nudge exists to make Roma SPEAK again,
    not to re-decide the call."""
    calls = []

    async def _extract(client, text, slot_name):
        from roma.controller.slots import DiscoveryValue

        calls.append(text)
        return DiscoveryValue()

    state = CallState(call_sid="t", **VARS)
    state.phase = "p2_discover"
    proc = PhaseControllerProcessor(
        state,
        client=object(),
        now_fn=lambda: __import__("datetime").datetime(2026, 7, 25, 10, 0),
        extract_discovery=_extract,
    )
    frame = _ctx(user=True)
    _drive(proc, [frame, frame])
    assert calls == ["haan ji"], "the machine advanced twice on one utterance"
    assert proc.repeat_skips == 1
    assert state.turn_count == 1


def test_a_genuinely_new_utterance_still_advances():
    """The guard is consecutive-only. Two different things said in a row are two turns."""
    calls = []

    async def _extract(client, text, slot_name):
        from roma.controller.slots import DiscoveryValue

        calls.append(text)
        return DiscoveryValue()

    state = CallState(call_sid="t", **VARS)
    state.phase = "p2_discover"
    proc = PhaseControllerProcessor(
        state,
        client=object(),
        now_fn=lambda: __import__("datetime").datetime(2026, 7, 25, 10, 0),
        extract_discovery=_extract,
    )
    _drive(proc, [_ctx(user=True)])
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "B.Tech kiya hai"}]
    _drive(proc, [LLMContextFrame(LLMContext(messages=msgs))])
    assert calls == ["haan ji", "B.Tech kiya hai"]
    assert proc.repeat_skips == 0
