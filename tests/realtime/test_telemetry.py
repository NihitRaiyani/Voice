import asyncio

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from roma.core.telemetry import Telemetry
from roma.realtime.telemetry import RealtimeTurnTelemetry, TelemetryFrameProbe


def test_frame_boundaries_measure_first_nonempty_output_and_forward_original_frames():
    async def run():
        now = [1.0]
        metrics = Telemetry(clock=lambda: now[0])
        turns = RealtimeTurnTelemetry(metrics)
        seen = []

        async def cross(boundary, frame):
            probe = TelemetryFrameProbe(turns, boundary=boundary, enable_direct_mode=True)

            async def push(original, direction):
                seen.append(original)

            probe.push_frame = push
            await probe.process_frame(frame, FrameDirection.DOWNSTREAM)
            assert seen[-1] is frame

        await cross("stt_input", VADUserStoppedSpeakingFrame(stop_secs=0.45))
        now[0] = 1.2
        await cross("stt_output", TranscriptionFrame("private@example.invalid", "", ""))
        context = LLMContextFrame(LLMContext())
        await cross("brain_input", context)
        now[0] = 1.3
        await cross("brain_output", context)
        await cross("llm_input", context)
        await cross("llm_output", LLMTextFrame(""))
        now[0] = 1.4
        await cross("llm_output", LLMTextFrame("private@example.invalid"))
        await cross("llm_output", LLMTextFrame("more"))
        await cross("tts_input", TextFrame("Safe text"))
        await cross("tts_output", TTSAudioRawFrame(b"", 16000, 1))
        now[0] = 1.6
        await cross("tts_output", TTSAudioRawFrame(b"\0\0", 16000, 1))
        now[0] = 2.0
        await cross("output", BotStoppedSpeakingFrame())
        rendered = metrics.render()
        for stage in ("endpointing", "stt", "brain", "llm_first", "tts_first", "turn"):
            assert (
                f'roma_latency_seconds_count{{stage="{stage}",outcome="ok",route="none"}} 1'
                in rendered
            )
        assert 'roma_latency_seconds_sum{stage="turn",outcome="ok",route="none"} 1' in rendered
        assert "private@example.invalid" not in rendered

    asyncio.run(run())


def test_interruption_cancels_pending_stage_and_late_frames_cannot_create_success():
    async def run():
        metrics = Telemetry()
        turns = RealtimeTurnTelemetry(metrics)
        turns.observe("llm_input", LLMContextFrame(LLMContext()))
        turns.observe("llm_output", InterruptionFrame())
        turns.observe("llm_output", LLMTextFrame("late"))
        turns.observe("output", BotStoppedSpeakingFrame())
        turns.observe("output", CancelFrame())
        body = metrics.render()
        assert 'stage="llm_first",outcome="cancelled"' in body
        assert 'stage="llm_first",outcome="ok"' not in body
        assert 'stage="turn",outcome="ok"' not in body

    asyncio.run(run())


def test_streamed_turn_waits_for_all_sentences_and_transport_response_drain():
    from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame

    now = [1.0]
    metrics = Telemetry(clock=lambda: now[0])
    turns = RealtimeTurnTelemetry(metrics)
    turns.observe("stt_input", VADUserStoppedSpeakingFrame(stop_secs=0.4))
    turns.observe("stt_output", TranscriptionFrame("Hello", "", ""))
    turns.observe("brain_input", LLMContextFrame(LLMContext()))
    turns.observe("brain_output", LLMFullResponseStartFrame())
    turns.observe("tts_input", LLMFullResponseStartFrame())
    turns.observe("tts_input", TextFrame("First sentence."))
    turns.observe("tts_output", TTSAudioRawFrame(b"\0\0", 8000, 1))
    now[0] = 2.0
    turns.observe("output", BotStoppedSpeakingFrame())
    assert 'stage="turn",outcome="ok"' not in metrics.render()
    turns.observe("tts_input", TextFrame("Second sentence."))
    turns.observe("tts_output", TTSAudioRawFrame(b"\0\0", 8000, 1))
    now[0] = 3.0
    turns.observe("output", BotStoppedSpeakingFrame())
    assert 'stage="turn",outcome="ok"' not in metrics.render()
    turns.observe("output", LLMFullResponseEndFrame())
    assert (
        'roma_latency_seconds_sum{stage="turn",outcome="ok",route="none"} 2' in metrics.render()
    )
    assert (
        'roma_latency_seconds_count{stage="tts_first",outcome="ok",route="none"} 1'
        in metrics.render()
    )
    assert 'stage="brain",outcome="ok"' in metrics.render()


def test_filler_audio_and_playback_stop_do_not_complete_a_turn_or_tts_latency():
    from pipecat.frames.frames import OutputAudioRawFrame

    metrics = Telemetry()
    turns = RealtimeTurnTelemetry(metrics)
    turns.observe("brain_input", LLMContextFrame(LLMContext()))
    turns.observe("tts_output", OutputAudioRawFrame(b"\0\0", 8000, 1))
    turns.observe("output", BotStoppedSpeakingFrame())
    assert 'stage="turn",outcome="ok"' not in metrics.render()
    assert 'stage="tts_first",outcome="ok"' not in metrics.render()
    turns.cancel()
    assert 'stage="turn",outcome="cancelled"' in metrics.render()


def test_durable_turn_timing_excludes_opening_and_freezes_controller_ordinal():
    state = [0, "open"]
    completed = []
    metrics = Telemetry(clock=lambda: 1.0)
    turns = RealtimeTurnTelemetry(
        metrics,
        capture_turn=lambda: tuple(state) if state[0] else None,
        on_complete=lambda identity, ms: completed.append(identity),
    )

    def complete():
        turns.observe("tts_output", TTSAudioRawFrame(b"\0\0", 16000, 1))
        turns.observe("output", BotStoppedSpeakingFrame())

    turns.observe("brain_input", LLMContextFrame(LLMContext()))
    turns.observe("brain_output", LLMContextFrame(LLMContext()))
    complete()
    assert completed == []
    turns.observe("stt_input", VADUserStoppedSpeakingFrame(stop_secs=0.1))
    state[:] = [1, "discover"]
    turns.observe("brain_input", LLMContextFrame(LLMContext()))
    turns.observe("brain_output", LLMContextFrame(LLMContext()))
    state[:] = [2, "value"]
    complete()
    assert completed == [(1, "discover")]
    turns.observe("stt_input", VADUserStoppedSpeakingFrame(stop_secs=0.1))
    turns.observe("brain_output", LLMContextFrame(LLMContext()))
    turns.observe("output", InterruptionFrame())
    complete()
    assert completed == [(1, "discover")]
