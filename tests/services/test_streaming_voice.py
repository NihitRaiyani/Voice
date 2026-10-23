import asyncio
from dataclasses import replace
from time import perf_counter

import pytest
from roma.core.ai_contracts import LLMChunk
from roma.domain.conversation.stage import ConversationStage
from roma.services.streaming_text import StreamConfig
from roma.services.voice_prototype import TimedLLMProvider
from tests.services.test_voice_prototype import setup, transcript


class StreamLLM:
    def __init__(self, first_audio=None, texts=None):
        self.first_audio = first_audio
        self.closed = False
        self.finished = False
        self.texts = texts or ("हम प्रैक्टिकल स्किल सीखते हैं।", "आपको किस विषय में रुचि है?")
    async def generate(self, request):
        try:
            for index, text in enumerate(self.texts):
                if index and self.first_audio is not None:
                    await asyncio.wait_for(self.first_audio.wait(), 2)
                yield LLMChunk(text)
            self.finished = True
            yield LLMChunk("", finish_reason="stop")
        finally:
            self.closed = True


def stream_voice(provider=None, **config):
    voice = setup()
    voice.state.stage = ConversationStage.VALUE
    voice.streaming = StreamConfig(**config)
    if provider:
        voice.conversation.provider = TimedLLMProvider(provider)
    return voice


def test_first_sentence_is_audible_before_second_sentence_is_generated():
    async def run():
        first = asyncio.Event()
        provider = StreamLLM(first)
        voice = stream_voice(provider)
        original = voice.speaker.play
        async def play(result):
            assert voice.store.records[-1][1]["status"] == "ready"
            if not first.is_set():
                assert not provider.finished
                voice.speaker.first_write_at = perf_counter()
                first.set()
            await original(result)
        voice.speaker.play = play
        result = await voice.respond(transcript("Please explain the practical classes", speech_end_at=perf_counter()))
        assert result.status == "played" and len(voice.speaker.played) == 2
        assert provider.closed and result.timings["speech_to_first_audio_ms"] >= 0
        assert all(row["peak_depth"] <= row["capacity"] for row in voice.queue_metrics.values())
    asyncio.run(run())


@pytest.mark.parametrize("text", ["फीस 50000 रुपये है।", "પગાર 30000 છે.", "salary 30000.",
                                  "फीस, 50000 रुपये है।"])
def test_unsafe_generated_chunks_never_reach_tts_and_provider_closes(text):
    async def run():
        provider = StreamLLM(texts=(text, "another unsafe sentence"))
        voice = stream_voice(provider)
        seen = []
        synthesize = voice.synthesis.synthesize
        async def synthesis(request, context, **kwargs):
            seen.append(request.text)
            return await synthesize(request, context, **kwargs)
        voice.synthesis.synthesize = synthesis
        await voice.respond(transcript("Please explain the practical classes"))
        assert seen and all("50000" not in value and "30000" not in value for value in seen)
        assert provider.closed
    asyncio.run(run())


def test_output_backpressure_fails_with_fallback_or_transport_close_not_silence():
    async def run():
        provider = StreamLLM(texts=tuple("यह प्रैक्टिकल क्लास है।" for _ in range(20)))
        voice = stream_voice(provider, capacity=2, high=2, low=0,
                             queue_timeout_secs=0.02, playback_timeout_secs=0.03)
        closed = asyncio.Event()
        async def blocked(result):
            await asyncio.Event().wait()
        async def close():
            closed.set()
        voice.speaker.play, voice.speaker.close = blocked, close
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(voice.respond(transcript("Please explain the practical classes")), 2)
        assert provider.closed and voice._poisoned
        assert closed.is_set()
        assert any(r["dropped_newest"] for r in voice.queue_metrics.values())
    asyncio.run(run())


def test_partials_are_bounded_and_never_advance_the_controller():
    from roma.services.streaming_voice import StreamingInput
    async def run():
        voice = stream_voice()
        inputs = StreamingInput(voice, capacity=2)
        for n in range(10):
            await inputs.partial(f"private partial {n}")
        assert inputs.partials.depth <= 2
        assert inputs.partials.metrics.dropped_oldest > 0
        assert voice.state.turn_count == 0 and not voice.store.records
        await inputs.close()
    asyncio.run(run())


def test_streaming_cancellation_discards_an_unfinished_clause():
    async def run():
        started = asyncio.Event()
        provider = StreamLLM()
        async def generate(request):
            try:
                yield LLMChunk("नमस्ते।")
                yield LLMChunk("फीस 50000")
                started.set()
                await asyncio.Event().wait()
            finally:
                provider.closed = True
        provider.generate = generate
        voice = stream_voice(provider)
        task = asyncio.create_task(voice.respond(transcript("Please explain the practical classes")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.closed
        assert not voice.synthesis._flights
        assert voice.store.records[-1][1]["error_code"] == "CANCELLED"
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["generation", "synthesis", "playback"])
def test_minimum_speech_barge_in_cancels_every_stage_and_resumes(phase):
    async def run():
        started, closed = asyncio.Event(), asyncio.Event()
        provider = StreamLLM()
        voice = stream_voice(provider)
        clears = []
        async def clear():
            clears.append("clear")
        voice.speaker.clear = clear
        if phase == "generation":
            async def generate(request):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield LLMChunk("never")
                finally:
                    provider.closed = True
            provider.generate = generate
        elif phase == "synthesis":
            async def synthesize(*args, **kwargs):
                try:
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            voice.synthesis.synthesize = synthesize
        else:
            async def play(result):
                try:
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            voice.speaker.play = play
        task = asyncio.create_task(voice.respond(transcript("Please explain the practical classes")))
        await asyncio.wait_for(started.wait(), 2)
        assert not await voice.observe_speech(0.99, 599)
        assert not task.done() and not clears
        assert await voice.observe_speech(0.99, 1)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(clears) == 1 and not voice._busy and not voice._poisoned
        assert voice.store.records[-1][1]["status"] == "interrupted"
        assert provider.closed
        if phase != "generation":
            assert closed.is_set()
        await voice.interrupt()
        assert len(clears) == 1  # idempotent when no turn is active
    asyncio.run(run())


def test_late_cancel_resistant_tts_result_cannot_send_after_clear():
    async def run():
        started = asyncio.Event()
        voice = stream_voice(StreamLLM())
        original = voice.synthesis.synthesize
        async def late(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return await original(*args, **kwargs)
        voice.synthesis.synthesize = late
        cleared = []
        async def clear():
            cleared.append(True)
        voice.speaker.clear = clear
        task = asyncio.create_task(voice.respond(transcript("Please explain the practical classes")))
        await started.wait()
        await voice.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleared and not voice.speaker.played
    asyncio.run(run())


def test_disconnect_uses_same_cancel_clear_release_and_marks_terminal_call():
    async def run():
        started = asyncio.Event()
        voice = stream_voice()
        events = []
        async def turn(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        async def clear():
            events.append("clear")
        async def release():
            events.append("release")
        voice.conversation.turn, voice.speaker.clear = turn, clear
        voice.release_on_disconnect = release
        task = asyncio.create_task(voice.respond(transcript()))
        await started.wait()
        await asyncio.gather(voice.disconnect(), voice.disconnect())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ["clear", "release"]
        assert voice.disconnected and voice.store.records[-1][1]["status"] == "disconnected"
        with pytest.raises(RuntimeError):
            await voice.respond(transcript())
    asyncio.run(run())


def test_cough_and_low_probability_echo_do_not_cancel():
    async def run():
        voice = stream_voice()
        assert not await voice.observe_speech(0.99, 80)
        assert not await voice.observe_speech(0.1, 900)
        assert not voice.disconnected
    asyncio.run(run())


def test_offline_streaming_cli_is_explicit_and_default_stays_sequential():
    import scripts.voice_prototype as cli
    assert not cli.parser().parse_args([]).streaming
    assert cli.parser().parse_args(["--streaming"]).streaming


def test_cached_filler_is_audited_and_never_synthesized_during_playback():
    async def run():
        voice = stream_voice()
        await voice.prime_filler()
        misses = voice.synthesis.misses
        assert await voice.play_cached_filler()
        assert voice.synthesis.misses == misses
    asyncio.run(run())


def test_interrupted_call_keeps_checkpoint_and_accepts_next_final_turn():
    async def run():
        started = asyncio.Event()
        provider = StreamLLM()
        async def blocked(request):
            try:
                started.set()
                await asyncio.Event().wait()
                yield LLMChunk("never")
            finally:
                provider.closed = True
        provider.generate = blocked
        voice = stream_voice(provider)
        task = asyncio.create_task(voice.respond(transcript("Please explain the practical classes")))
        await started.wait()
        await voice.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = await voice.conversation.store.load(voice.state.call_sid)
        assert saved is not None and saved.turn_count == voice.state.turn_count
        voice.conversation.provider = TimedLLMProvider(StreamLLM())
        result = await voice.respond(transcript("Please continue with the practical classes"))
        assert result.status == "played" and result.attempt == 2
        assert voice.store.records[-1][1]["status"] == "played"
    asyncio.run(run())


def test_websocket_disconnect_from_audio_source_cancels_active_turn():
    from roma.services.streaming_voice import StreamingInput
    from starlette.websockets import WebSocketDisconnect
    from tests.services.test_voice_prototype import transcript
    async def run():
        started = asyncio.Event()
        voice = stream_voice()
        async def turn(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        voice.conversation.turn = turn
        cleared = []
        async def clear():
            cleared.append(True)
        voice.speaker.clear = clear
        class Speech:
            class Scorer:
                def probability(self, pcm):
                    return 0.0
                def reset(self):
                    pass
            scorer = Scorer()
            async def feed(self, pcm):
                return (transcript(),)
            async def finish(self, **kwargs):
                return ()
            def reset(self):
                pass
        async def source():
            yield b"\0\0" * 320
            await started.wait()
            raise WebSocketDisconnect()
        await StreamingInput(voice).run(source(), Speech())
        assert voice.disconnected and len(cleared) == 1
        assert voice.store.records[-1][1]["status"] == "disconnected"
    asyncio.run(run())


def test_streaming_cpu_framing_is_byte_identical_to_existing_tts_path():
    from roma.services.tts_service import build_mulaw_frames
    async def run():
        voice = stream_voice()
        from roma.core.ai_contracts import TTSRequest
        from roma.domain.safety.audit import SafetyContext
        speech = await voice.synthesis.synthesize(TTSRequest("नमस्ते।", language="hi"), SafetyContext(voice.state.call_sid, 1, "value"))
        assert build_mulaw_frames(speech.result.audio, speech.result.format.sample_rate_hz) == speech.mulaw_frames
    asyncio.run(run())


def test_clear_failure_still_releases_resources_and_disconnects():
    async def run():
        voice = stream_voice()
        events = []
        async def clear():
            raise OSError("private transport failure")
        async def release():
            events.append("release")
        async def close():
            events.append("close")
        voice.speaker.clear, voice.speaker.close = clear, close
        voice.release_on_disconnect = release
        with pytest.raises(RuntimeError):
            await voice.disconnect()
        assert "release" in events and "close" in events and voice.disconnected
    asyncio.run(run())


def test_barge_in_on_real_vad_frames_keeps_final_transcript_consumer_alive():
    from roma.eval.speech import ScriptedProbability, ScriptedSTT
    from roma.services.speech_input import SpeechInputService
    from roma.services.streaming_voice import StreamingInput
    async def run():
        ready = asyncio.Event()
        provider = StreamLLM()
        calls = 0
        async def generate(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                ready.set()
                await asyncio.Event().wait()
            yield LLMChunk("हम प्रैक्टिकल स्किल सीखते हैं।", finish_reason="stop")
        provider.generate = generate
        voice = stream_voice(provider, minimum_speech_ms=128)
        clears = []
        async def clear():
            clears.append(True)
        voice.speaker.clear = clear
        speech = SpeechInputService(ScriptedSTT(("Please explain practical classes", "Please continue with classes")), ScriptedProbability(), voice.state)
        async def source():
            for speaking in [1] * 5 + [0] * 20:
                yield bytes([speaking, 0]) * 512
                await asyncio.sleep(0)
            await ready.wait()
            for speaking in [1] * 8 + [0] * 20:
                yield bytes([speaking, 0]) * 512
                await asyncio.sleep(0)
        results = await asyncio.wait_for(StreamingInput(voice).run(source(), speech), 4)
        assert len(clears) == 1 and len(results) == 1 and results[0].status == "played"
        assert voice.attempt == 2
    asyncio.run(run())


def test_streaming_tts_audit_failure_remains_visible_in_safe_error_code():
    async def run():
        voice = stream_voice(StreamLLM())
        original = voice.synthesis.synthesize
        async def unavailable(*args, **kwargs):
            speech = await original(*args, **kwargs)
            return replace(speech, audit_ok=False)
        voice.synthesis.synthesize = unavailable
        result = await voice.respond(transcript("Please explain the practical classes"))
        assert result.error_code == "SAFETY"
    asyncio.run(run())


def test_no_confirmed_booking_claim_can_stream_before_database_commit():
    async def run():
        voice = stream_voice(StreamLLM(texts=("आपका अपॉइंटमेंट कन्फर्म है।",)))
        voice.state.booking_required = True
        seen = []
        original = voice.synthesis.synthesize
        async def synthesize(request, context, **kwargs):
            seen.append(request.text)
            return await original(request, context, **kwargs)
        voice.synthesis.synthesize = synthesize
        await voice.respond(transcript("Please explain the practical classes"))
        assert seen and all("आपका अपॉइंटमेंट कन्फर्म है" not in line for line in seen)
        assert voice.state.appointment_id is None
    asyncio.run(run())


def test_capture_overflow_closes_transport_and_records_input_loss():
    from roma.services.streaming_voice import StreamingInput
    async def run():
        voice = stream_voice()
        events = []
        async def clear():
            events.append("clear")
        async def close():
            events.append("close")
        voice.speaker.clear, voice.speaker.close = clear, close
        class Speech:
            scorer = type("Scorer", (), {"probability": lambda self, pcm: 0.0, "reset": lambda self: None})()
            async def feed(self, pcm):
                await asyncio.Event().wait()
            async def finish(self, **kwargs):
                return ()
            def reset(self):
                pass
        async def source():
            for _ in range(20):
                yield b"\0\0" * 320
        inputs = StreamingInput(voice, capacity=2)
        with pytest.raises(ExceptionGroup):
            await inputs.run(source(), Speech())
        assert voice.disconnected and events == ["clear", "close"]
        assert inputs.audio.metrics.dropped_oldest > 0
    asyncio.run(run())


def test_stream_input_obeys_existing_five_minute_session_deadline():
    from roma.services.streaming_voice import StreamingInput
    async def run():
        voice = stream_voice()
        voice._started = voice.clock() - 301
        class Speech:
            scorer = type("Scorer", (), {"probability": lambda self, pcm: 0.0, "reset": lambda self: None})()
            def reset(self):
                pass
        async def source():
            await asyncio.Event().wait()
            yield b"\0\0"
        with pytest.raises(RuntimeError, match=r"^CAPTURE:"):
            await asyncio.wait_for(StreamingInput(voice).run(source(), Speech()), 0.2)
        assert voice.disconnected
    asyncio.run(run())


def test_cancel_resistant_fallback_never_plays_after_interruption():
    async def run():
        voice = stream_voice(StreamLLM(), cleanup_timeout_secs=0.01)
        entered, release = asyncio.Event(), asyncio.Event()
        original = voice.synthesis.synthesize
        calls = 0
        async def synthesize(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("synthesis failed")
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return await original(*args, **kwargs)
        voice.synthesis.synthesize = synthesize
        task = asyncio.create_task(voice.respond(transcript("Please explain practical classes")))
        await asyncio.wait_for(entered.wait(), 1)
        await voice.interrupt()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not voice.speaker.played
    asyncio.run(run())


def test_failed_close_of_cancel_resistant_turn_still_finishes_disconnect():
    async def run():
        voice = stream_voice(cleanup_timeout_secs=0.01)
        entered, release = asyncio.Event(), asyncio.Event()
        events = []
        async def turn(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            raise asyncio.CancelledError
        async def close():
            events.append("close")
            raise OSError("device unavailable")
        async def clear():
            events.append("clear")
        async def release_resources():
            events.append("release")
        async def finish(*args, **kwargs):
            events.append("finish")
        voice.conversation.turn = turn
        voice.speaker.close, voice.speaker.clear = close, clear
        voice.release_on_disconnect, voice.store.finish = release_resources, finish
        task = asyncio.create_task(voice.respond(transcript("Please explain practical classes")))
        await entered.wait()
        try:
            await voice.disconnect()
        except RuntimeError:
            pass
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert events == ["close", "clear", "release", "finish"]
        assert voice.disconnected
    asyncio.run(run())


def test_next_response_waits_until_previous_interrupt_clear_finishes():
    async def run():
        voice = stream_voice(StreamLLM())
        started, clearing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = voice.conversation.turn
        async def blocked(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        async def clear():
            clearing.set()
            await release.wait()
        voice.conversation.turn, voice.speaker.clear = blocked, clear
        first = asyncio.create_task(voice.respond(transcript("Please explain practical classes")))
        await started.wait()
        interrupt = asyncio.create_task(voice.interrupt())
        await clearing.wait()
        voice.conversation.turn = original
        second = asyncio.create_task(voice.respond(transcript("Please continue practical classes")))
        try:
            await asyncio.sleep(0.02)
            assert not voice.speaker.played and not second.done()
        finally:
            release.set()
            await asyncio.gather(first, interrupt, second, return_exceptions=True)
        assert voice.speaker.played
    asyncio.run(run())


def test_cancel_resistant_ready_receipt_never_plays_after_interruption():
    async def run():
        voice = stream_voice(StreamLLM(), cleanup_timeout_secs=0.01)
        entered, release = asyncio.Event(), asyncio.Event()
        original = voice.store.save
        async def save(sid, record):
            if record["status"] == "ready":
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
            await original(sid, record)
        voice.store.save = save
        task = asyncio.create_task(voice.respond(transcript("Please explain practical classes")))
        await entered.wait()
        await voice.interrupt()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not voice.speaker.played
    asyncio.run(run())
