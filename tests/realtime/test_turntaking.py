"""Step 5B turn strategies — the backchannel guard and adaptive endpointing (docs/05).

These run entirely offline: the strategies are driven frame-by-frame with a bare pipecat
`TaskManager`, no transport and no STT. Async via `asyncio.run` (repo convention — there is
no pytest-asyncio).

Because these sit on the barge-in / cancellation path (docs/08), the concurrency-shaped
cases are pinned explicitly: arm/cancel idempotency, double-fire, and the
`on_reset_aggregation` guard.
"""

import asyncio
import time

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.utils.asyncio.task_manager import TaskManager

from roma.telephony.backchannel import (
    BACKCHANNEL_MAX_SECS,
    ENDPOINT_CONTINUATION_SECS,
    ENDPOINT_DEFAULT_SECS,
    ENDPOINT_TERMINAL_SECS,
)
from roma.telephony.turntaking import (
    AdaptiveEndpointStopStrategy,
    BackchannelAwareUserTurnStartStrategy,
)

START_SECS = 0.2
STOP_SECS = 0.85
ARM = 0.05


def _text(s: str) -> TranscriptionFrame:
    return TranscriptionFrame(user_id="lead", text=s, timestamp="", finalized=True)


def _vad_pair(span: float, t0: float = 100.0):
    """A VAD start/stop pair describing exactly `span` seconds of speech."""
    return (
        VADUserStartedSpeakingFrame(start_secs=START_SECS, timestamp=t0 + START_SECS),
        VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=t0 + span + STOP_SECS),
    )


class _Controller:
    """Stand-in for `UserTurnController`, faithful on the two behaviours that matter.

    It reproduces `_trigger_user_turn_start`'s `if self._user_turn: return` guard — the
    reason this strategy replaces `VADUserTurnStartStrategy` rather than joining it — and
    it calls `handle_user_turn_started` back into the strategy from inside the event, just
    as the real controller does. Without that callback the strategy never learns its own
    turn started and the tests drift from production.
    """

    def __init__(self):
        self.starts = 0
        self.resets = 0
        self.user_turn = False
        self._strategy = None

    def attach(self, strategy):
        self._strategy = strategy

        async def on_started(_strategy, _params):
            if self.user_turn:
                return
            self.user_turn = True
            self.starts += 1
            await strategy.handle_user_turn_started()

        async def on_reset(_strategy):
            self.resets += 1

        strategy.add_event_handler("on_user_turn_started", on_started)
        strategy.add_event_handler("on_reset_aggregation", on_reset)
        return self

    async def end_turn(self):
        self.user_turn = False
        await self._strategy.handle_user_turn_stopped()


async def _make(arm: float = BACKCHANNEL_MAX_SECS, **kwargs):
    """Build a wired-up strategy. `arm` defaults to the real 600ms threshold; fast-arm
    tests pass a short one so the timer resolves inside the test."""
    strategy = BackchannelAwareUserTurnStartStrategy(
        enable_interruptions=True, backchannel_max_secs=arm, **kwargs
    )
    await strategy.setup(TaskManager())
    return strategy, _Controller().attach(strategy)


async def _send(strategy, *frames):
    last = None
    for f in frames:
        last = await strategy.process_frame(f)
    return last


def test_short_backchannel_over_roma_does_not_start_a_turn():
    """docs/05: <600ms + in-lexicon + Roma mid-utterance => log it, keep talking."""

    async def run():
        s, rec = await _make()
        start, stop = _vad_pair(0.4)
        await _send(s, BotStartedSpeakingFrame(), start, stop)
        result = await _send(s, _text("હા"))
        await s.cleanup()
        return rec, result

    rec, result = asyncio.run(run())
    assert rec.starts == 0
    assert result == ProcessFrameResult.CONTINUE


def test_suppression_never_resets_aggregation():
    """The suppressed text must survive.

    `trigger_reset_aggregation()` wipes the WHOLE aggregation buffer, not the last
    segment. A lone `હા` answering P1 while Roma finishes the question would be dropped —
    the exact live stall the Gujarati affirmation fix cured. Only the interruption is
    suppressed, never the text.
    """

    async def run():
        s, rec = await _make()
        start, stop = _vad_pair(0.3)
        await _send(s, BotStartedSpeakingFrame(), start, stop, _text("hmm"))
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.resets == 0


def test_content_word_over_roma_barges_in():
    """Short span, but not a backchannel — the lead is taking the turn."""

    async def run():
        s, rec = await _make()
        start, stop = _vad_pair(0.4)
        await _send(s, BotStartedSpeakingFrame(), start, stop)
        result = await _send(s, _text("હા પણ મોંઘું છે"))
        await s.cleanup()
        return rec, result

    rec, result = asyncio.run(run())
    assert rec.starts == 1
    assert result == ProcessFrameResult.STOP


def test_backchannel_while_roma_silent_starts_a_turn():
    """The P1 regression guard.

    `હા` answering a question Roma has finished asking is an ANSWER, not a nod. docs/05
    condition (c) fails, so the guard must not engage — and the turn must start at VAD
    onset, exactly as Step 3 behaves today.

    "FINISHED asking" is the load-bearing word, and it is why the BotStoppedSpeakingFrame
    below is not decoration. The strategy starts life owing the opening line (outbound call,
    Roma speaks first), so until she has actually delivered a turn there IS something to
    interrupt — see `_bot_busy`.
    """

    async def run():
        s, rec = await _make()
        await _send(s, BotStoppedSpeakingFrame())
        start, _ = _vad_pair(0.3)
        result = await _send(s, start)
        await s.cleanup()
        return rec, result

    rec, result = asyncio.run(run())
    assert rec.starts == 1
    assert result == ProcessFrameResult.STOP


def test_no_vad_means_no_suppression():
    """Offline paths run `build_vad_fn=lambda: None`, so no VAD frames ever arrive and the
    span is unknown. Unknown must never suppress — never silently drop lead speech."""

    async def run():
        s, rec = await _make()
        await _send(s, BotStartedSpeakingFrame())
        result = await _send(s, _text("હા"))
        await s.cleanup()
        return rec, result

    rec, result = asyncio.run(run())
    assert rec.starts == 1
    assert result == ProcessFrameResult.STOP


def test_no_suppression_once_a_turn_is_active():
    """Mid-turn backchannels are just more of the lead's turn — nothing left to guard."""

    async def run():
        s, rec = await _make()
        await _send(s, BotStoppedSpeakingFrame())
        start, _ = _vad_pair(0.3)
        await _send(s, start)
        before = rec.starts
        start2, stop2 = _vad_pair(0.2, t0=110.0)
        await _send(s, BotStartedSpeakingFrame(), start2, stop2, _text("હા"))
        await s.cleanup()
        return rec, before

    rec, before = asyncio.run(run())
    assert before == 1
    assert rec.starts == 1


def test_long_span_barges_in_without_any_transcript():
    """The load-bearing case.

    Sarvam emits no interims and flushes only on VAD stop (p99 TTFS 1.17s), so a
    transcript-gated barge-in would land after the lead already stopped. A span at or over
    the threshold fails docs/05 condition (a) on its own, so VAD alone barges in — here,
    with zero transcript frames sent.
    """

    async def run():
        s, rec = await _make(arm=ARM)
        start, _ = _vad_pair(2.0)
        await _send(s, BotStartedSpeakingFrame(), start)
        await asyncio.sleep(ARM * 4)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 1


def test_long_span_barges_in_even_on_a_backchannel_word():
    """A drawn-out `હાાાા` over Roma is a turn-take: condition (a) fails, and (a) is
    evaluated by the fast arm before the words are ever available."""

    async def run():
        s, rec = await _make(arm=ARM)
        start, _ = _vad_pair(2.0)
        await _send(s, BotStartedSpeakingFrame(), start)
        await asyncio.sleep(ARM * 4)
        stop = VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=103.0)
        await _send(s, stop, _text("હા"))
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 1


def test_vad_stop_inside_the_window_cancels_the_fast_arm():
    """Speech that ends before the threshold must fall through to the words, not barge in
    on the timer."""

    async def run():
        s, rec = await _make(arm=ARM)
        start, stop = _vad_pair(0.4)
        await _send(s, BotStartedSpeakingFrame(), start)
        await _send(s, stop)
        await asyncio.sleep(ARM * 4)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 0


def test_roma_going_quiet_promotes_a_pending_arm_immediately():
    """If Roma stops talking while the arm is pending, condition (c) has failed — the lead
    is just talking. Start the turn now rather than making them wait out the timer."""

    async def run():
        s, rec = await _make(arm=ARM)
        start, _ = _vad_pair(2.0)
        await _send(s, BotStartedSpeakingFrame(), start)
        result = await _send(s, BotStoppedSpeakingFrame())
        await asyncio.sleep(ARM * 4)
        await s.cleanup()
        return rec, result

    rec, result = asyncio.run(run())
    assert rec.starts == 1
    assert result == ProcessFrameResult.STOP


def test_rearming_supersedes_the_pending_arm_and_fires_once():
    """Two VAD starts without an intervening stop (possible under jitter) must leave one
    live timer, not two."""

    async def run():
        s, rec = await _make(arm=ARM)
        start1, _ = _vad_pair(2.0)
        start2, _ = _vad_pair(2.0, t0=100.5)
        await _send(s, BotStartedSpeakingFrame(), start1, start2)
        await asyncio.sleep(ARM * 4)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 1


def test_cancel_arm_is_safe_to_call_repeatedly():
    """Turn-boundary callbacks and cleanup all cancel the arm; overlapping cancels of the
    same task must not raise."""

    async def run():
        s, _ = await _make(arm=ARM)
        start, _ = _vad_pair(2.0)
        await _send(s, BotStartedSpeakingFrame(), start)
        await asyncio.gather(
            s.handle_user_turn_started(),
            s.handle_user_turn_stopped(),
            s.cleanup(),
        )
        assert s._arm_task is None
        await s.cleanup()

    asyncio.run(run())


def test_fast_arm_does_not_fire_after_the_turn_already_started():
    """The transcript arm winning the race must not be followed by a duplicate trigger
    from the timer."""

    async def run():
        s, rec = await _make(arm=ARM)
        start, _ = _vad_pair(2.0)
        await _send(s, BotStartedSpeakingFrame(), start)
        await _send(s, _text("મને ઓનલાઇન જોઈએ"))
        await asyncio.sleep(ARM * 4)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 1


def test_stale_bot_speaking_cannot_survive_a_turn_boundary():
    """A dropped BotStoppedSpeakingFrame must not leave the guard suppressing the NEXT
    turn's one-word answer."""

    async def run():
        s, rec = await _make()
        await _send(s, BotStartedSpeakingFrame())
        await s.handle_user_turn_started()
        await rec.end_turn()
        start, stop = _vad_pair(0.3, t0=120.0)
        await _send(s, start, stop, _text("હા"))
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 1


async def _timeout_after(*texts: str) -> float:
    s = AdaptiveEndpointStopStrategy()
    await s.setup(TaskManager())
    for t in texts:
        await s._handle_transcription(_text(t))
    timeout = s._user_speech_timeout
    await s.cleanup()
    return timeout


async def _armed_sleeps(text: str, *, since_vad_stop: float) -> list[float]:
    """Drive the REAL production order and record what the timer was actually armed with.

    The order matters and is the whole point: Sarvam flushes its socket only on
    `VADUserStoppedSpeakingFrame`, so the VAD stop ALWAYS lands before the transcript. The
    base class arms the user-speech timer at the VAD stop by binding `_user_speech_timeout`
    as a task argument — so a subclass that merely assigns the attribute when the
    transcript arrives changes nothing at all, and every turn silently runs at 850ms.

    `_timeout_after` above cannot see that: it calls `_handle_transcription` directly with
    no VAD stop, so it only proves the *number* is computed. This helper proves it is
    *armed*.

    Args:
        since_vad_stop: how long ago the VAD stop happened, in seconds. The timer is a
            deadline measured from that stop, so the re-arm must sleep the remainder.
    """
    armed: list[float] = []
    s = AdaptiveEndpointStopStrategy()
    await s.setup(TaskManager())

    def _record(timeout: float):
        armed.append(timeout)
        return asyncio.sleep(3600)

    s._user_speech_timeout_handler = _record

    await s.handle_user_turn_started()
    start, _ = _vad_pair(0.0)
    await s.process_frame(start)
    await s.process_frame(
        VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=time.time() - since_vad_stop)
    )
    await s.process_frame(_text(text))
    await s.cleanup()
    return armed


def test_adaptive_timeout_is_actually_armed_not_just_assigned():
    """THE regression guard for this strategy — the sibling of the VAD span-formula test.

    A terminal answer must SHORTEN the wait that is really running. Before the deadline
    re-arm, this test failed with `armed == [0.85]`: the transcript updated the attribute
    and nothing re-read it, so the 500ms floor never reached a timer. No exception, no
    failing test, just every turn quietly back at the default.
    """
    armed = asyncio.run(_armed_sleeps("હા", since_vad_stop=0.3))

    assert len(armed) == 2, f"expected an initial arm and a re-arm, got {armed}"
    assert armed[0] == ENDPOINT_DEFAULT_SECS
    assert armed[1] == pytest.approx(ENDPOINT_TERMINAL_SECS - 0.3, abs=0.05)
    assert armed[1] < ENDPOINT_TERMINAL_SECS


def test_adaptive_continuation_extends_the_running_timer():
    """The other direction: a continuation marker must push the deadline out past 850ms."""
    armed = asyncio.run(_armed_sleeps("એક મિનિટ", since_vad_stop=0.3))

    assert armed[0] == ENDPOINT_DEFAULT_SECS
    assert armed[1] == pytest.approx(ENDPOINT_CONTINUATION_SECS - 0.3, abs=0.05)
    assert 0.3 + armed[1] == pytest.approx(ENDPOINT_CONTINUATION_SECS, abs=0.05)


def test_adaptive_deadline_already_passed_arms_zero_not_negative():
    """A transcript slower than the shortened deadline must fire at once, never sleep a
    negative (which asyncio treats as 0 anyway) or wrap into a long wait."""
    armed = asyncio.run(_armed_sleeps("હા", since_vad_stop=2.0))

    assert armed[1] == 0.0


def test_adaptive_policy_value_survives_the_rearm():
    """The re-arm hands `_restart_user_speech_timer` a remainder. That must not stick: a
    second VAD stop later in the same turn has to arm the full policy floor again."""

    async def run():
        s = AdaptiveEndpointStopStrategy()
        await s.setup(TaskManager())
        armed: list[float] = []

        def _record(timeout: float):
            armed.append(timeout)
            return asyncio.sleep(3600)

        s._user_speech_timeout_handler = _record
        await s.handle_user_turn_started()
        start, _ = _vad_pair(0.0)
        await s.process_frame(start)
        await s.process_frame(
            VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=time.time() - 0.3)
        )
        await s.process_frame(_text("એક મિનિટ"))
        policy = s._user_speech_timeout
        await s.cleanup()
        return policy

    assert asyncio.run(run()) == ENDPOINT_CONTINUATION_SECS


@pytest.mark.parametrize(
    "text,expected",
    [
        ("હા", ENDPOINT_TERMINAL_SECS),
        ("theek hai", ENDPOINT_TERMINAL_SECS),
        ("મને ઓનલાઇન જોઈએ", ENDPOINT_DEFAULT_SECS),
        ("એક મિનિટ", ENDPOINT_CONTINUATION_SECS),
        ("હા તો", ENDPOINT_CONTINUATION_SECS),
    ],
)
def test_adaptive_timeout_from_transcript(text, expected):
    assert asyncio.run(_timeout_after(text)) == expected


def test_adaptive_timeout_uses_cumulative_text():
    """`_text` accumulates across segments, so the tail of the WHOLE turn decides — a
    terminal word in segment 1 must not shorten the endpoint after segment 2 continues."""
    assert asyncio.run(_timeout_after("હા ", "તો")) == ENDPOINT_CONTINUATION_SECS
    assert asyncio.run(_timeout_after("હા ", "મને વિચારવું છે")) == ENDPOINT_DEFAULT_SECS


def test_adaptive_timeout_resets_to_default_on_a_new_turn():
    """A previous turn's 1300ms must not leak into the next one."""

    async def run():
        s = AdaptiveEndpointStopStrategy()
        await s.setup(TaskManager())
        await s._handle_transcription(_text("એક મિનિટ"))
        stretched = s._user_speech_timeout
        await s.handle_user_turn_started()
        fresh = s._user_speech_timeout
        await s.cleanup()
        return stretched, fresh

    stretched, fresh = asyncio.run(run())
    assert stretched == ENDPOINT_CONTINUATION_SECS
    assert fresh == ENDPOINT_DEFAULT_SECS


def test_default_is_our_tuned_span_not_pipecats():
    """The point of the test is unchanged — the strategy must take OUR span, not pipecat's
    stock 600ms. Ours is docs/05's 850ms again as of 2026-07-31; see
    `test_endpoint_spans_are_the_live_tuned_numbers` for why it was lowered and restored."""
    assert AdaptiveEndpointStopStrategy()._user_speech_timeout == ENDPOINT_DEFAULT_SECS
    assert ENDPOINT_DEFAULT_SECS == 0.85


def test_pipecat_upgrade_canary():
    """`AdaptiveEndpointStopStrategy` overrides a PRIVATE base method and writes a PRIVATE
    base attribute. If pipecat renames either, the override silently stops being called
    and every turn quietly reverts to a fixed timeout — no error, no failing behaviour
    test. Fail loudly here instead.
    """
    from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
        SpeechTimeoutUserTurnStopStrategy,
    )

    assert hasattr(SpeechTimeoutUserTurnStopStrategy, "_handle_transcription")
    assert hasattr(SpeechTimeoutUserTurnStopStrategy, "_restart_user_speech_timer")
    assert hasattr(SpeechTimeoutUserTurnStopStrategy, "_user_speech_timeout_handler")
    assert (
        AdaptiveEndpointStopStrategy._handle_transcription
        is not SpeechTimeoutUserTurnStopStrategy._handle_transcription
    )

    state = vars(SpeechTimeoutUserTurnStopStrategy(**{}))
    for attr in (
        "_user_speech_timeout",
        "_user_speech_timeout_task",
        "_user_speech_wait_done",
        "_vad_stopped_time",
    ):
        assert attr in state, attr

    import inspect

    src = inspect.getsource(SpeechTimeoutUserTurnStopStrategy._restart_user_speech_timer)
    assert "self._user_speech_timeout_handler(self._user_speech_timeout)" in src


def test_injected_user_speech_timeout_is_honoured_as_the_per_turn_default():
    """`handle_user_turn_started` used to reset to the module constant unconditionally, so
    a `user_speech_timeout=` passed at construction was discarded from the first turn on —
    the knob looked wired and did nothing. docs/10 Step 7 tunes this on real audio."""

    async def run():
        s = AdaptiveEndpointStopStrategy(user_speech_timeout=0.7)
        await s.setup(TaskManager())
        await s.handle_user_turn_started()
        after_turn_start = s._user_speech_timeout
        await s._handle_transcription(_text("એક મિનિટ"))
        stretched = s._user_speech_timeout
        await s.handle_user_turn_started()
        reset = s._user_speech_timeout
        await s.cleanup()
        return after_turn_start, stretched, reset

    after_turn_start, stretched, reset = asyncio.run(run())
    assert after_turn_start == 0.7
    assert stretched == ENDPOINT_CONTINUATION_SECS
    assert reset == 0.7


def test_multi_segment_text_does_not_fuse_tokens_at_the_boundary():
    """pipecat's base does a bare `self._text += frame.text`, so `...છે` + `એક મિનિટ`
    became `છેએક મિનિટ` and the 2-token tail check missed the continuation marker.
    Inert on Sarvam (one final per turn), live on any STT that streams partials."""

    async def run():
        s = AdaptiveEndpointStopStrategy()
        await s.setup(TaskManager())
        await s._handle_transcription(_text("મને જોઈએ છે"))
        await s._handle_transcription(_text("એક મિનિટ"))
        timeout = s._user_speech_timeout
        await s.cleanup()
        return timeout

    assert asyncio.run(run()) == ENDPOINT_CONTINUATION_SECS


def _real_user_aggregator(**settings_kw):
    """The user aggregator exactly as `media.py` builds it with barge-in ON."""
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

    from roma.telephony.media import build_user_params

    class _Settings:
        backchannel_max_secs = ARM
        endpoint_default_secs = ENDPOINT_DEFAULT_SECS
        endpoint_terminal_secs = ENDPOINT_TERMINAL_SECS
        endpoint_continuation_secs = ENDPOINT_CONTINUATION_SECS

    for k, v in settings_kw.items():
        setattr(_Settings, k, v)

    pair = LLMContextAggregatorPair(
        LLMContext(messages=[{"role": "system", "content": "s"}]),
        user_params=build_user_params(True, _Settings()),
    )
    return pair.user()


def _strategy_of(user_agg):
    controller = user_agg._user_turn_controller
    return controller.user_turn_strategies.start[0], controller


def test_the_configured_strategy_is_ours_and_carries_the_tuned_values():
    """`media.py` used to call `build_user_params(settings.enable_barge_in)` with no
    `settings`, so every Step-7 knob was read from Settings, printed in the teardown line,
    and never applied. A dial that looks wired and moves nothing is worse than no dial."""
    strategy, _ = _strategy_of(_real_user_aggregator(backchannel_max_secs=0.42))
    assert isinstance(strategy, BackchannelAwareUserTurnStartStrategy)
    assert strategy._backchannel_max_secs == 0.42


def test_the_stop_strategy_also_receives_the_tuned_values():
    agg = _real_user_aggregator(endpoint_default_secs=0.77, endpoint_terminal_secs=0.33)
    stop = agg._user_turn_controller.user_turn_strategies.stop[0]
    assert isinstance(stop, AdaptiveEndpointStopStrategy)
    assert stop._default_user_speech_timeout == 0.77
    assert stop._terminal_secs == 0.33


def test_our_strategy_is_the_ONLY_start_strategy():
    """`VADUserTurnStartStrategy` fires at speech onset, before any transcript exists, and
    `UserTurnController._trigger_user_turn_start` opens with `if self._user_turn: return` —
    so leaving it in the list makes this guard dead code that still looks wired up."""
    _, controller = _strategy_of(_real_user_aggregator())
    assert len(controller.user_turn_strategies.start) == 1


def _drive_real(frames, *, wait=0.0):
    """Push frames through the real aggregator and controller, as the pipeline would."""

    started = []

    async def run():
        agg = _real_user_aggregator()
        await agg._user_turn_controller.setup(TaskManager())
        strategy, controller = _strategy_of(agg)

        real_trigger = strategy.trigger_user_turn_started

        async def _spy():
            started.append(time.time())
            await real_trigger()

        strategy.trigger_user_turn_started = _spy

        for direction, frame in frames:
            await controller.process_frame(frame)
            _ = direction
        if wait:
            await asyncio.sleep(wait)
        await controller.cleanup()

    asyncio.run(run())
    return started


def test_a_bot_speaking_frame_actually_reaches_the_strategy():
    """The suspicion after the live call: `BotStartedSpeakingFrame` is pushed by
    `transport.output()`, which sits DOWNSTREAM of the user aggregator — so does the
    aggregator ever see it? Pipecat pushes bot-speaking frames both downstream AND upstream
    (`base_output._bot_started_speaking`), and the aggregator hands EVERY frame to its turn
    controller. If either of those ever changes, `_bot_speaking` silently stays False, the
    fast arm never arms, and barge-in becomes dead code with a green test suite."""

    async def run():
        agg = _real_user_aggregator()
        await agg._user_turn_controller.setup(TaskManager())
        strategy, controller = _strategy_of(agg)

        await controller.process_frame(BotStartedSpeakingFrame())
        assert strategy._bot_speaking is True

        await controller.process_frame(BotStoppedSpeakingFrame())
        assert strategy._bot_speaking is False
        await controller.cleanup()

    asyncio.run(run())


def test_speech_over_roma_longer_than_the_threshold_DOES_barge_in():
    """The end-to-end claim, against the real controller: Roma is speaking, the lead talks
    over her for longer than `backchannel_max_secs`, and a user turn starts."""
    started = _drive_real(
        [
            (None, BotStartedSpeakingFrame()),
            (None, VADUserStartedSpeakingFrame(start_secs=START_SECS, timestamp=100.0)),
        ],
        wait=ARM * 3,
    )
    assert started, "the fast arm never fired — barge-in is dead"


def test_a_short_backchannel_over_roma_does_NOT_barge_in():
    started = _drive_real(
        [
            (None, BotStartedSpeakingFrame()),
            (None, VADUserStartedSpeakingFrame(start_secs=START_SECS, timestamp=100.0)),
            (None, VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=100.1)),
            (None, _text("achha")),
        ],
        wait=ARM * 3,
    )
    assert not started, "a nod cut Roma off"


def test_a_transcript_arriving_after_roma_starts_STILL_takes_the_turn():
    """The lead's turn closed on the endpoint timer, Roma began replying, and only then did
    the transcript land. It is new content and it must be answered, not swallowed."""

    async def run():
        strategy, controller = await _make()

        await _send(strategy, *_vad_pair(span=0.3))
        controller.user_turn = False
        await strategy.handle_user_turn_stopped()
        await _send(strategy, BotStartedSpeakingFrame())
        before = controller.starts

        await _send(strategy, _text("mujhe evening batch chahiye"))

        assert controller.starts == before + 1, "the lead's words were dropped"
        await strategy.cleanup()

    asyncio.run(run())


def test_speech_that_started_after_roma_still_interrupts():
    """The ordinary barge-in, unchanged."""

    async def run():
        strategy, controller = await _make()
        await _send(strategy, BotStartedSpeakingFrame())
        await _send(strategy, *_vad_pair(span=0.3, t0=time.time()))
        await _send(strategy, _text("ek minute suniye"))
        assert controller.starts == 1
        await strategy.cleanup()

    asyncio.run(run())


def test_a_backchannel_is_still_the_only_thing_suppressed_over_roma():
    """The narrow suppression docs/05 actually specifies stays; nothing else was added."""

    async def run():
        strategy, controller = await _make(arm=ARM)
        await _send(strategy, BotStartedSpeakingFrame(), *_vad_pair(span=0.02))
        await _send(strategy, _text("achha"))
        assert controller.starts == 0
        await strategy.cleanup()

    asyncio.run(run())


def test_noise_before_roma_has_ever_spoken_does_not_start_a_turn():
    """THE regression. On an outbound call Roma speaks first, so a VAD onset before she
    has said anything is noise on a line nobody is talking on — never a turn-take."""

    async def run():
        s, rec = await _make()
        start, _ = _vad_pair(0.3)
        await _send(s, start)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 0, "line noise cancelled Roma's opening turn"


def test_noise_between_the_lead_finishing_and_roma_speaking_does_not_start_a_turn():
    """The same window mid-call: the lead has stopped, Roma owes a reply, and the LLM and
    TTS together took 5.5s to get audio onto the wire on the live call."""

    async def run():
        s, rec = await _make()
        await _send(s, BotStoppedSpeakingFrame())
        start, _ = _vad_pair(0.3)
        await _send(s, start)
        await s.handle_user_turn_started()
        await s.handle_user_turn_stopped()
        before = rec.starts

        blip, _ = _vad_pair(0.2, t0=200.0)
        await _send(s, blip)
        await s.cleanup()
        return rec, before

    rec, before = asyncio.run(run())
    assert rec.starts == before, "noise cancelled the reply Roma was generating"


def test_sustained_speech_while_roma_owes_a_reply_still_takes_the_turn():
    """The other half, and the reason the mid-call window is an arm rather than a block: a
    lead who really is talking must not be stranded just because Roma has not reached the
    wire yet. Sustained speech always wins after `backchannel_max_secs`.

    Mid-call, so the opening lock-out is already lifted — that one is deliberately NOT
    releasable, because before Roma speaks there is nothing to interrupt."""

    async def run():
        s, rec = await _make(0.05)
        await _send(s, BotStartedSpeakingFrame(), BotStoppedSpeakingFrame())
        await s.handle_user_turn_stopped()
        start, _ = _vad_pair(0.3)
        await _send(s, start)
        await asyncio.sleep(0.15)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 1, "a real speaker was stranded by the pending-reply guard"


def test_the_lead_talking_into_the_silence_cannot_cancel_the_opening():
    """THE regression from live call CA20c70f7 (2026-07-27).

    The lead said "hello?" into a silent line. VAD detected it correctly, the fast arm
    released after 0.6s, and the resulting barge-in cancelled the opening GENERATION — the
    log shows one `Generating chat from context`, no `llm usage`, and no TTS at all. So
    Roma never spoke, so the lead said "hello?" again. A deadlock that cannot break itself.

    Sustained speech deliberately does NOT release this one, unlike the mid-call pending
    window: there is nothing to interrupt. `OpeningTurnGuard` keeps their words and replays
    them once she has finished opening, so nothing is lost.
    """

    async def run():
        s, rec = await _make(0.05)
        start, _ = _vad_pair(2.4)
        await _send(s, start)
        await asyncio.sleep(0.15)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 0, "the lead's 'hello?' cancelled Roma's opening generation"


def test_the_lock_out_lifts_the_moment_roma_is_on_the_wire():
    """It must lift at BotSTARTED, not BotStopped, or the lead could never interrupt Roma's
    very first sentence — which is precisely the sentence worth interrupting."""

    async def run():
        s, rec = await _make(0.05)
        await _send(s, BotStartedSpeakingFrame())
        start, _ = _vad_pair(2.4)
        await _send(s, start)
        await asyncio.sleep(0.15)
        await s.cleanup()
        return rec

    rec = asyncio.run(run())
    assert rec.starts == 1, "barge-in on the opening sentence was blocked"


def test_a_second_short_attempt_over_roma_barges_in():
    """Call b67b25da. The lead said "wait wait wait wait wait ... मैं कब का बोल रहा हूँ फिर भी
    बोले जा रहे हो" and Roma talked through all of it — 3 barge-ins in a four-minute call.

    "wait" is ~300ms, so every burst cancelled the 600ms fast arm before it expired and the
    duration gate never saw a qualifying span. Persistence is the signal the duration missed:
    nobody backchannels twice while being talked over."""

    async def run():
        s, rec = await _make()
        await _send(s, BotStartedSpeakingFrame())

        first_start, first_stop = _vad_pair(0.3, t0=100.0)
        await _send(s, first_start, first_stop)
        after_first = rec.starts

        second_start, _ = _vad_pair(0.3, t0=101.0)
        result = await _send(s, second_start)
        await s.cleanup()
        return rec, after_first, result

    rec, after_first, result = asyncio.run(run())
    assert after_first == 0, "one short sound is still an acknowledgement"
    assert rec.starts == 1, "the second attempt must interrupt"
    assert result == ProcessFrameResult.STOP


def test_the_attempt_count_resets_on_each_new_bot_turn():
    """Per bot turn, not per call. Two acks spread over a whole call are two acks, and
    counting them together would make Roma jumpy."""

    async def run():
        s, rec = await _make()

        await _send(s, BotStartedSpeakingFrame())
        a, b = _vad_pair(0.3, t0=100.0)
        await _send(s, a, b)
        await _send(s, BotStoppedSpeakingFrame())
        await rec.end_turn()

        await _send(s, BotStartedSpeakingFrame())
        c, d = _vad_pair(0.3, t0=200.0)
        await _send(s, c, d)
        starts = rec.starts
        await s.cleanup()
        return starts

    assert asyncio.run(run()) == 0, "a single ack in each of two turns must not barge in"


def test_one_long_span_still_barges_in_on_the_first_attempt():
    """The duration gate is not replaced — a lead who simply starts talking over Roma
    should not have to say a second thing first."""

    async def run():
        s, rec = await _make(arm=ARM)
        await _send(s, BotStartedSpeakingFrame())
        start, _ = _vad_pair(1.0, t0=100.0)
        await _send(s, start)
        await asyncio.sleep(ARM * 3)
        await s.cleanup()
        return rec.starts

    assert asyncio.run(run()) == 1
