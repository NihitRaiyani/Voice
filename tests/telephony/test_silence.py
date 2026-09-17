"""Dead-air watchdog (roma.telephony.silence).

Live call CA8a85a4f (2026-07-28): "koi bol kyun nahi raha hai", three times. 13 LLM calls
produced 9 TTS outputs and one gap ran 21 seconds.

Driven inline rather than through `run_test`: the watchdog deliberately emits a frame on a
TIMER with no input frame to trigger it, which the harness cannot express, and repeated
`run_test` calls against one processor deadlock (see test_pretts.py).
"""

import asyncio

from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMRunFrame,
    StartFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

from roma.telephony.silence import SILENCE_LIMIT_SECS, SilenceWatchdog
from roma.telephony.turnflight import TurnFlight

LIMIT = 0.2


async def _watchdog(flight=None):
    """A real TaskManager, because the timer IS the mechanism here — unlike `CallCloser`,
    which degrades to its frame path when there is none."""
    w = SilenceWatchdog(limit_secs=LIMIT, flight=flight, enable_direct_mode=True)
    await w.setup(
        FrameProcessorSetup(
            clock=SystemClock(), task_manager=TaskManager(), pipeline_worker=None
        )
    )
    w.pushed = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        w.pushed.append(frame)

    w.push_frame = _capture
    return w


async def _feed(w, frames, settle=0.0):
    for f in frames:
        await w.process_frame(f, FrameDirection.DOWNSTREAM)
    if settle:
        await asyncio.sleep(settle)


def _nudges(w):
    return [f for f in w.pushed if isinstance(f, LLMRunFrame)]


def test_silence_after_roma_was_cut_off_restarts_her():
    """THE bug. Barge-in cancels her TTS on 0.5s of VAD; the noise gate then drops the
    transcript, so no user turn starts and nothing restarts her. On CA8a85a4f that left
    21 seconds of silence and the lead asked whether anyone was on the call."""

    async def run():
        w = await _watchdog()
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame()])
        await _feed(
            w,
            [VADUserStartedSpeakingFrame(), InterruptionFrame(), VADUserStoppedSpeakingFrame()],
        )
        await asyncio.sleep(LIMIT * 4)
        assert len(_nudges(w)) == 1, "Roma was never restarted"
        await w.cleanup()

    asyncio.run(run())


def test_it_nudges_once_per_silence_not_repeatedly():
    """A second LLMRunFrame while the first is still generating is Roma talking over
    herself — a worse artefact than the gap it would be covering."""

    async def run():
        w = await _watchdog()
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame(), InterruptionFrame()])
        await asyncio.sleep(LIMIT * 8)
        assert len(_nudges(w)) == 1
        await w.cleanup()

    asyncio.run(run())


def test_a_second_interruption_arms_it_again():
    async def run():
        w = await _watchdog()
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame(), InterruptionFrame()])
        await asyncio.sleep(LIMIT * 4)
        await _feed(w, [BotStoppedSpeakingFrame(), InterruptionFrame()])
        await asyncio.sleep(LIMIT * 4)
        assert len(_nudges(w)) == 2
        await w.cleanup()

    asyncio.run(run())


def test_a_lead_thinking_after_a_finished_turn_is_never_nudged():
    """THE regression, and it was mine. The first version armed on every line Roma spoke, so
    four seconds of ordinary thinking time read as a fault: on CA79ed16d it fired TWELVE
    times in two minutes, re-running her turn each time. Nine consecutive lines offered the
    identical pair of slots and the lead's complaint was that she repeats herself.

    She finished her turn here and was never cut off. There is nothing to rescue."""

    async def run():
        w = await _watchdog()
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame()])
        await asyncio.sleep(LIMIT * 10)
        assert _nudges(w) == [], "nudged a lead who was simply thinking"
        await w.cleanup()

    asyncio.run(run())


def test_an_interruption_that_roma_answers_disarms_it():
    """Cut off, then she speaks again — the turn recovered on its own and the watchdog has
    no business firing afterwards."""

    async def run():
        w = await _watchdog()
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame(), InterruptionFrame()])
        await _feed(w, [BotStoppedSpeakingFrame()])
        await asyncio.sleep(LIMIT * 6)
        assert _nudges(w) == []
        await w.cleanup()

    asyncio.run(run())


def test_it_stays_quiet_while_the_lead_is_talking():
    """A lead thinking mid-sentence is not dead air. Interrupting them to ask if they are
    still there is exactly the tic this is meant to remove."""

    async def run():
        w = await _watchdog()
        await _feed(
            w,
            [
                StartFrame(),
                BotStoppedSpeakingFrame(),
                InterruptionFrame(),
                VADUserStartedSpeakingFrame(),
            ],
        )
        await asyncio.sleep(LIMIT * 6)
        assert _nudges(w) == []
        await w.cleanup()

    asyncio.run(run())


def test_it_stays_quiet_before_roma_has_ever_spoken():
    """The opening turn belongs to `PickupGreeter`. Two processors racing to start the call
    would greet the lead twice, which is a bug this repo has already had."""

    async def run():
        w = await _watchdog()
        await _feed(w, [StartFrame()])
        await asyncio.sleep(LIMIT * 6)
        assert _nudges(w) == []
        await w.cleanup()

    asyncio.run(run())


def test_a_reply_in_flight_is_never_nudged():
    """THE bug on CA4ba2a6b8, and it cost the whole call.

    The previous fix disarmed on `TranscriptionFrame` and was dead code — `LLMUserAggregator`
    consumes transcripts and never pushes them downstream, so this processor, which sits past
    the output transport, never saw one. The nudge fired 4.9s after a transcript, a second
    generation started on top of the first, the TTS context was torn down mid-stream, and
    every audio frame after that was discarded for having no context. The call went silent.

    So the answer is TOLD to it now, by the processor that can see a turn begin."""

    async def run():
        flight = TurnFlight()
        w = await _watchdog(flight=flight)
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame(), InterruptionFrame()])
        flight.turn_started()
        await asyncio.sleep(LIMIT * 12)
        assert _nudges(w) == [], "nudged a turn that was already being answered"
        await w.cleanup()

    asyncio.run(run())


def test_a_dropped_transcript_still_gets_the_nudge():
    """The gate must not cost the watchdog its actual job. The failure it exists for is a
    cough the noise gate refuses: no user turn ever starts, so `TurnFlight` stays empty and
    the nudge is exactly right."""

    async def run():
        flight = TurnFlight()
        w = await _watchdog(flight=flight)
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame()])
        await _feed(
            w,
            [VADUserStartedSpeakingFrame(), InterruptionFrame(), VADUserStoppedSpeakingFrame()],
        )
        await asyncio.sleep(LIMIT * 6)
        assert len(_nudges(w)) == 1
        await w.cleanup()

    asyncio.run(run())


def test_a_turn_that_never_produced_audio_stops_blocking_the_nudge():
    """The safety valve. A generation that dies without ever reaching TTS would otherwise pin
    `pending` True for the rest of the call and permanently silence the one processor whose
    job is rescuing that exact case."""

    async def run():
        flight = TurnFlight(max_pending_secs=LIMIT)
        w = await _watchdog(flight=flight)
        await _feed(w, [StartFrame(), BotStoppedSpeakingFrame(), InterruptionFrame()])
        flight.turn_started()
        await asyncio.sleep(LIMIT * 6)
        assert len(_nudges(w)) == 1
        await w.cleanup()

    asyncio.run(run())


def test_roma_speaking_closes_the_turn_and_times_it():
    """The watchdog is also where the end-to-end measurement stops: it is the only processor
    that sees audio reach the wire."""

    async def run():
        flight = TurnFlight()
        w = await _watchdog(flight=flight)
        await _feed(w, [StartFrame()])
        flight.turn_started()
        await asyncio.sleep(0.05)
        await _feed(w, [TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=8000, num_channels=1)])
        assert flight.pending is False
        s = flight.summary()
        assert s["n"] == 1 and s["p50"] >= 0.05
        await w.cleanup()

    asyncio.run(run())


def test_the_shipped_limit_clears_the_stt_finalize_window():
    """With the transcript disarming, this only times the case where no reply is coming. What
    it must still clear is the interruption-to-transcript window — STT finalize, p50 1.15s on
    CA3bf8d51c. Four seconds was the old value and sat BELOW that call's p50
    transcript-to-reply time of 3.60s, which is why it misfired on merely-slow turns."""
    assert SILENCE_LIMIT_SECS >= 5.0
