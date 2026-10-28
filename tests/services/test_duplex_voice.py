import asyncio
from types import SimpleNamespace

from roma.core.turn_epoch import TurnEpoch
from roma.domain.audio.endpointing import EndpointConfig, EndpointDetector
from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import STTResult
from roma.services.duplex_voice import CaptureFrame, DuplexVoiceSession
from roma.services.speech_input import SpeechInputService
from roma.services.streaming_text import StreamConfig


class Scorer:
    def __init__(self):
        self.frames = 0

    def probability(self, pcm):
        self.frames += 1
        return .95 if any(pcm) else 0

    def reset(self):
        pass


class Provider:
    def __init__(self):
        self.requests = []

    async def transcribe(self, request):
        self.requests.append(request)
        return STTResult('caller', 'en', turn_epoch=request.turn_epoch)


class Voice:
    def __init__(self):
        self.epochs = TurnEpoch()
        self.streaming = StreamConfig(minimum_speech_ms=200)
        self.clock = lambda: 0.0
        self._started = 0
        self._response_task = None
        self.disconnected = False
        self.results = []
        self.interrupts = []
        self.speaker = SimpleNamespace(close=self.close)
        self.closed = 0

    async def respond(self, final):
        self.epochs.require_current(final.turn_epoch)
        self.results.append(final)
        return final

    async def interrupt(self, *, owner=None, turn_epoch=None):
        self.epochs.require_current(turn_epoch)
        self.interrupts.append(turn_epoch)
        if owner is not None:
            owner.cancel()
        return True

    async def disconnect(self):
        self.disconnected = True
        self.epochs.advance()

    async def close(self):
        self.closed += 1


def speech(provider=None):
    return SpeechInputService(provider or Provider(), Scorer(), CallState(),
        endpoint=EndpointDetector(EndpointConfig(silence_ms=160, hangover_ms=32)))


async def blocks(voice, values):
    for i, value in enumerate(values):
        yield CaptureFrame(bytes([value, 0]) * 320, (i + 1) * .02,
                           voice.epochs.snapshot(), 0)
        await asyncio.sleep(0)


def test_two_normal_turns_are_decoded_and_responded_with_distinct_current_epochs():
    async def run():
        voice, service = Voice(), speech()
        session = DuplexVoiceSession(voice, service)
        outcomes = await session.run(blocks(voice, [1] * 12 + [0] * 12 + [2] * 12 + [0] * 12))
        assert len(outcomes) == 2
        assert [f.turn_epoch for f in voice.results] == [1, 2]
        assert all(r.turn_epoch == i for i, r in enumerate(service.provider.requests, 1))
        assert not voice.disconnected
    asyncio.run(run())


def test_capture_recovery_discards_partial_speech_and_decodes_only_fresh_generation():
    async def run():
        voice, service = Voice(), speech()
        session = DuplexVoiceSession(voice, service)

        async def source():
            async for frame in blocks(voice, [1] * 8):
                yield frame
            await session.recover_capture()
            for index, value in enumerate([2] * 12 + [0] * 12):
                yield CaptureFrame(bytes([value, 0]) * 320, .18 + index * .02,
                                   voice.epochs.snapshot(), 1)
                await asyncio.sleep(0)

        outcomes = await session.run(source())
        assert len(outcomes) == len(voice.results) == len(service.provider.requests) == 1
        assert service.provider.requests[0].audio.count(b'\2\0') == 12 * 320
        assert b'\1\0' not in service.provider.requests[0].audio
        assert voice.results[0].turn_epoch == 3 and not voice.disconnected

    asyncio.run(run())


def test_capture_recovery_rejects_late_cancel_resistant_asr_and_keeps_fresh_turn():
    class LateProvider(Provider):
        def __init__(self):
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()
            self.cancelled = False

        async def transcribe(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                self.started.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    await self.release.wait()
            return STTResult('caller', 'en', turn_epoch=request.turn_epoch)

    async def run():
        voice, provider = Voice(), LateProvider()
        service = speech(provider)
        session = DuplexVoiceSession(voice, service)

        async def source():
            async for frame in blocks(voice, [1] * 12 + [0] * 12):
                yield frame
            await provider.started.wait()
            asyncio.get_running_loop().call_later(.01, provider.release.set)
            await session.recover_capture()
            for index, value in enumerate([2] * 12 + [0] * 12):
                yield CaptureFrame(bytes([value, 0]) * 320, 1 + index * .02,
                                   voice.epochs.snapshot(), 1)
                await asyncio.sleep(0)

        outcomes = await session.run(source())
        assert provider.cancelled and len(provider.requests) == 2
        assert len(outcomes) == len(voice.results) == 1
        assert voice.results[0].turn_epoch == 3 and not voice.disconnected
        assert provider.requests[1].audio.count(b'\2\0') == 12 * 320

    asyncio.run(run())


def test_playback_barge_retains_the_entire_caller_onset_and_runs_fast_vad():
    class PlayingVoice(Voice):
        def __init__(self):
            super().__init__()
            self.playing = asyncio.Event()

        async def respond(self, final):
            if final.turn_epoch == 1:
                self.playing.set()
                await asyncio.Event().wait()
            return await super().respond(final)

    async def run():
        voice, service = PlayingVoice(), speech()
        async def source():
            i = 0
            for value in [1] * 12 + [0] * 12:
                i += 1
                yield CaptureFrame(bytes([value, 0]) * 320, i * .02, voice.epochs.snapshot(), 0)
                await asyncio.sleep(0)
            await voice.playing.wait()
            for value in [2] * 16 + [0] * 12:
                i += 1
                yield CaptureFrame(bytes([value, 0]) * 320, i * .02, voice.epochs.snapshot(), 0)
                await asyncio.sleep(0)
        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert len(outcomes) == 1
        assert voice.interrupts == [2]
        assert [f.turn_epoch for f in voice.results] == [2]
        assert service.provider.requests[1].audio.count(b'\2\0') == 16 * 320
        assert service.scorer.frames >= 30
    asyncio.run(run())


def test_short_overlap_keeps_playback_and_never_becomes_a_controller_turn():
    class PlayingVoice(Voice):
        def __init__(self):
            super().__init__()
            self.streaming = StreamConfig()
            self.playing, self.release = asyncio.Event(), asyncio.Event()

        async def respond(self, final):
            self.playing.set()
            await self.release.wait()
            return await super().respond(final)

    async def run():
        voice, service = PlayingVoice(), speech()

        async def source():
            i = 0
            for turn in range(2):
                if turn:
                    await voice.playing.wait()
                # A 400 ms acknowledgement is shorter than the existing
                # 600 ms sustained-speech interruption threshold.
                for value in [turn + 1] * 20 + [0] * 12:
                    i += 1
                    yield CaptureFrame(bytes([value, 0]) * 320, i * .02,
                                       voice.epochs.snapshot(), 0)
                    await asyncio.sleep(0)
            assert voice.epochs.snapshot() == 1
            assert not voice.interrupts
            assert len(service.provider.requests) == 1
            voice.release.set()

        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert len(outcomes) == 1
        assert [result.turn_epoch for result in voice.results] == [1]
        assert not voice.disconnected

    asyncio.run(run())


def test_sustained_overlap_at_default_threshold_interrupts_and_retains_all_speech():
    class PlayingVoice(Voice):
        def __init__(self):
            super().__init__()
            self.streaming = StreamConfig()
            self.playing = asyncio.Event()

        async def respond(self, final):
            if final.turn_epoch == 1:
                self.playing.set()
                await asyncio.Event().wait()
            return await super().respond(final)

    async def run():
        voice, service = PlayingVoice(), speech()

        async def source():
            i = 0
            for turn, duration in enumerate((12, 32)):
                if turn:
                    await voice.playing.wait()
                for value in [turn + 1] * duration + [0] * 12:
                    i += 1
                    yield CaptureFrame(bytes([value, 0]) * 320, i * .02,
                                       voice.epochs.snapshot(), 0)
                    await asyncio.sleep(0)

        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert [result.turn_epoch for result in outcomes] == [2]
        assert voice.interrupts == [2]
        assert service.provider.requests[1].audio.count(b'\2\0') == 32 * 320
        assert not voice.disconnected

    asyncio.run(run())


def test_short_reply_after_response_finishes_is_preserved_at_default_threshold():
    async def run():
        voice, service = Voice(), speech()
        voice.streaming = StreamConfig()
        outcomes = await DuplexVoiceSession(voice, service).run(
            blocks(voice, [1] * 12 + [0] * 12 + [2] * 4 + [0] * 12))
        assert [result.turn_epoch for result in outcomes] == [1, 2]
        assert service.provider.requests[1].audio.count(b'\2\0') == 4 * 320
        assert not voice.interrupts

    asyncio.run(run())

def test_short_overlap_that_outlasts_response_end_does_not_become_consent():
    class PlayingVoice(Voice):
        def __init__(self):
            super().__init__()
            self.streaming = StreamConfig()
            self.playing, self.release = asyncio.Event(), asyncio.Event()

        async def respond(self, final):
            self.playing.set()
            await self.release.wait()
            return await super().respond(final)

    async def run():
        voice, service = PlayingVoice(), speech()

        async def source():
            i = 0
            for value in [1] * 12 + [0] * 12:
                i += 1
                yield CaptureFrame(bytes([value, 0]) * 320, i * .02, voice.epochs.snapshot(), 0)
                await asyncio.sleep(0)
            await voice.playing.wait()
            for index, value in enumerate([2] * 20 + [0] * 12):
                if index == 2:
                    voice.release.set()
                i += 1
                yield CaptureFrame(bytes([value, 0]) * 320, i * .02, voice.epochs.snapshot(), 0)
                await asyncio.sleep(0)

        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert len(outcomes) == len(service.provider.requests) == 1
        assert not voice.interrupts

    asyncio.run(run())


def test_fast_vad_continues_while_cancellation_resistant_old_asr_finishes():
    class Blocked(Provider):
        def __init__(self):
            super().__init__()
            self.started, self.cancelled, self.release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def transcribe(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                self.started.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    await self.release.wait()
            return STTResult('caller', 'en', turn_epoch=request.turn_epoch)

    async def run():
        voice, provider = Voice(), Blocked()
        service = speech(provider)
        async def source():
            i = 0
            for value in [1] * 12 + [0] * 12:
                i += 1
                yield CaptureFrame(bytes([value, 0]) * 320, i * .02, voice.epochs.snapshot(), 0)
                await asyncio.sleep(0)
            await provider.started.wait()
            before = service.scorer.frames
            for value in [2] * 16 + [0] * 12:
                i += 1
                yield CaptureFrame(bytes([value, 0]) * 320, i * .02, voice.epochs.snapshot(), 0)
                await asyncio.sleep(0)
            assert service.scorer.frames >= before + 17
            assert provider.cancelled.is_set()
            assert not voice.results
            provider.release.set()
        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert len(outcomes) == 1
        assert [f.turn_epoch for f in voice.results] == [2]
        assert provider.requests[1].audio.count(b'\2\0') == 16 * 320
    asyncio.run(run())


def test_lost_stale_frame_inside_an_utterance_fails_closed_without_stitching():
    import pytest

    async def run():
        voice, service = Voice(), speech()
        async def source():
            async for frame in blocks(voice, [1] * 8):
                yield frame
            yield CaptureFrame(b'\3\0' * 320, .18, 0, 0)
            yield CaptureFrame(b'\2\0' * 320, .20, voice.epochs.snapshot(), 0)
        with pytest.raises(ExceptionGroup):
            await DuplexVoiceSession(voice, service).run(source())
        assert voice.disconnected and voice.closed == 1
        assert not voice.results and not service.provider.requests
    asyncio.run(run())


def test_aec_cleaned_silence_during_playback_does_not_create_a_caller_turn():
    async def run():
        voice, service = Voice(), speech()
        assert await DuplexVoiceSession(voice, service).run(blocks(voice, [0] * 100)) == []
        assert not voice.interrupts and not service.provider.requests
    asyncio.run(run())


def test_expired_physical_deadline_starts_no_capture_or_provider_and_closes():
    import pytest
    from roma.core.voice_errors import VoiceFailure

    async def run():
        voice, service = Voice(), speech()
        captured = []
        with pytest.raises(VoiceFailure):
            await DuplexVoiceSession(voice, service, remaining=lambda: 0,
                on_capture_started=lambda: captured.append(True)).run(blocks(voice, [1] * 12))
        assert not captured and not service.provider.requests and not voice.results
        assert voice.disconnected and voice.closed == 1
    asyncio.run(run())


def test_max_turns_stops_capture_before_side_effects_and_leaves_successful_finish_to_caller():
    async def run():
        voice, service = Voice(), speech()
        captured, delivered, closed = [], [], []
        async def source():
            try:
                async for frame in blocks(voice, [1] * 12 + [0] * 12):
                    yield frame
                while True:
                    await asyncio.sleep(0)
                    captured.append(True)
                    yield CaptureFrame(b'\0\0' * 320, .48 + len(captured) * .02,
                                       voice.epochs.snapshot(), 0)
            finally:
                closed.append(True)
        outcomes = await DuplexVoiceSession(voice, service, max_turns=1,
            on_capture_started=lambda: delivered.append('started'),
            on_result=lambda result: delivered.append(result)).run(source())
        assert len(outcomes) == len(voice.results) == 1
        assert len(delivered) == 2 and delivered[0] == 'started'
        assert not voice.disconnected and voice.closed == 0 and closed == [True]
        assert len(service.provider.requests) == 1
    asyncio.run(run())


def test_thirty_second_style_hard_boundary_is_final_and_keeps_every_original_sample():
    async def run():
        voice, service = Voice(), speech()
        # A short cap exercises the same forced-final path without thirty seconds
        # of synthetic frames; pre-roll plus onset still fit inside the bound.
        service.endpoint = EndpointDetector(EndpointConfig(silence_ms=160, hangover_ms=32,
                                                         max_utterance_ms=512))
        await DuplexVoiceSession(voice, service).run(blocks(voice, [1] * 41))
        assert len(service.provider.requests) == 2
        assert sum(len(r.audio) for r in service.provider.requests) == 41 * 640
        assert all(len(r.audio) <= 512 * 32 for r in service.provider.requests)
        assert voice.results[0].reason == 'deadline'
    asyncio.run(run())


def test_pending_decode_overflow_closes_and_never_emits_late_asr():
    import pytest

    class Resistant(Provider):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()

        async def transcribe(self, request):
            self.requests.append(request)
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    pass
            return STTResult('late', 'en', turn_epoch=request.turn_epoch)

    async def run():
        voice, provider = Voice(), Resistant()
        service = speech(provider)
        async def release():
            await asyncio.sleep(.02)
            provider.release.set()
        async with asyncio.TaskGroup() as group:
            group.create_task(release())
            with pytest.raises(ExceptionGroup):
                await DuplexVoiceSession(voice, service).run(
                    blocks(voice, ([1] * 12 + [0] * 12) * 6))
        assert voice.disconnected and voice.closed == 1
        assert not voice.results
        assert len(provider.requests) == 1
    asyncio.run(run())


def test_repeated_onset_while_clear_is_pending_never_starts_a_stale_response():
    class SlowClear(Voice):
        def __init__(self):
            super().__init__()
            self.playing, self.clearing, self.release = (asyncio.Event() for _ in range(3))

        async def respond(self, final):
            if final.turn_epoch == 1:
                self.playing.set()
                await asyncio.Event().wait()
            assert self.release.is_set()
            return await super().respond(final)

        async def interrupt(self, *, owner=None, turn_epoch=None):
            await super().interrupt(owner=owner, turn_epoch=turn_epoch)
            self.clearing.set()
            await self.release.wait()
            return True

    async def run():
        voice, service = SlowClear(), speech()
        async def source():
            i = 0
            for turn in range(3):
                if turn == 1:
                    await voice.playing.wait()
                for value in [turn + 1] * 16 + [0] * 12:
                    i += 1
                    yield CaptureFrame(bytes([value, 0]) * 320, i * .02,
                                       voice.epochs.snapshot(), 0)
                    await asyncio.sleep(0)
                if turn == 1:
                    await voice.clearing.wait()
            assert service.scorer.frames >= 52
            assert not voice.results
            voice.release.set()
        result = await DuplexVoiceSession(voice, service).run(source())
        assert [f.turn_epoch for f in result] == [3]
        assert voice.interrupts == [2]
        assert service.provider.requests[-1].audio.count(b'\3\0') == 16 * 320
    asyncio.run(run())


def test_cancelled_session_does_not_deliver_final_after_control_swallows_cancellation():
    import pytest

    class ResistantClear(Voice):
        def __init__(self):
            super().__init__()
            self.playing, self.clearing, self.cancelled, self.release = (
                asyncio.Event() for _ in range(4))

        async def respond(self, final):
            if final.turn_epoch == 1:
                self.playing.set()
                await asyncio.Event().wait()
            return await super().respond(final)

        async def interrupt(self, *, owner=None, turn_epoch=None):
            await super().interrupt(owner=owner, turn_epoch=turn_epoch)
            self.clearing.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
            return True

    async def run():
        voice, service = ResistantClear(), speech()
        captured = asyncio.Event()

        async def source():
            i = 0
            for turn in range(2):
                if turn:
                    await voice.playing.wait()
                for value in [turn + 1] * 16 + [0] * 12:
                    i += 1
                    yield CaptureFrame(bytes([value, 0]) * 320, i * .02,
                                       voice.epochs.snapshot(), 0)
                    await asyncio.sleep(0)
            # Give the independent decoder/result consumer a chance to reach
            # its control join before the owner cancels the whole session.
            for _ in range(10):
                await asyncio.sleep(0)
            captured.set()

        task = asyncio.create_task(DuplexVoiceSession(voice, service).run(source()))
        await asyncio.wait_for(captured.wait(), 1)
        await asyncio.wait_for(voice.clearing.wait(), 1)
        assert len(service.provider.requests) == 2
        task.cancel()
        await asyncio.wait_for(voice.cancelled.wait(), 1)
        voice.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not voice.results
        assert voice.disconnected

    asyncio.run(run())


def test_real_voice_interrupt_preserves_preclaimed_epoch_for_next_caller():
    from dataclasses import replace

    import pytest
    from tests.services.test_streaming_voice import StreamLLM, stream_voice
    from tests.services.test_voice_prototype import transcript

    async def run():
        voice = stream_voice(StreamLLM())
        playing = asyncio.Event()
        original_play = voice.speaker.play
        async def play(result):
            playing.set()
            await asyncio.Event().wait()
        voice.speaker.play = play
        first = asyncio.create_task(voice.respond(transcript('haan')))
        await playing.wait()
        epoch = voice.epochs.advance()
        assert await voice.interrupt(owner=first, turn_epoch=epoch)
        with pytest.raises(asyncio.CancelledError):
            await first
        assert voice.epochs.snapshot() == epoch
        voice.speaker.play = original_play
        outcome = await voice.respond(replace(transcript('haan'), turn_epoch=epoch))
        assert outcome.status == 'played'
        assert voice.epochs.snapshot() == epoch
    asyncio.run(run())


def test_claiming_near_end_turn_marks_interruption_before_ready_old_llm_resumes():
    from roma.core.ai_contracts import LLMChunk
    from tests.services.test_streaming_voice import StreamLLM, stream_voice
    from tests.services.test_voice_prototype import transcript

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        provider = StreamLLM()
        async def generate(request):
            started.set()
            await release.wait()
            yield LLMChunk('यह प्रैक्टिकल क्लास है।')
        provider.generate = generate
        voice = stream_voice(provider)
        first = asyncio.create_task(voice.respond(transcript('haan')))
        await started.wait()
        release.set()  # Old response is ready before asynchronous clear can run.
        epoch = voice.claim_turn()
        control = asyncio.create_task(voice.interrupt(owner=first, turn_epoch=epoch))
        await asyncio.gather(first, control, return_exceptions=True)
        assert not voice._poisoned and not voice.disconnected
        assert voice.epochs.snapshot() == epoch
    asyncio.run(run())


def test_real_voice_late_tts_from_interrupted_epoch_cannot_play():
    from tests.services.test_streaming_voice import StreamLLM, stream_voice

    class YesProvider(Provider):
        async def transcribe(self, request):
            self.requests.append(request)
            return STTResult('haan', 'en', turn_epoch=request.turn_epoch)

    async def run():
        voice = stream_voice(StreamLLM(texts=('यह प्रैक्टिकल क्लास है।',)), minimum_speech_ms=200)
        service = speech(YesProvider())
        synthesizing, release, rejected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = voice.synthesis.synthesize
        first = True
        async def synthesize(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                synthesizing.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    rejected.set()
                    await release.wait()
            return await original(*args, **kwargs)
        voice.synthesis.synthesize = synthesize
        async def source():
            i = 0
            for turn in range(2):
                if turn:
                    await synthesizing.wait()
                for value in [1] * 16 + [0] * 12:
                    i += 1
                    yield CaptureFrame(bytes([value, 0]) * 320, i * .02,
                                       voice.epochs.snapshot(), 0)
                    await asyncio.sleep(0)
            assert rejected.is_set() and not voice.speaker.played
            release.set()
        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert len(outcomes) == 1 and outcomes[0].status == 'played'
        assert voice.speaker.played
        assert all(result.turn_epoch == 2 for result in voice.speaker.played)
        assert not voice._poisoned
    asyncio.run(run())


def test_real_voice_max_turns_does_not_finish_store_as_failed_before_caller_pause():
    from tests.services.test_streaming_voice import StreamLLM, stream_voice

    async def run():
        voice = stream_voice(StreamLLM(texts=('यह प्रैक्टिकल क्लास है।',)))
        service, finished = speech(), []
        async def finish(*args, **kwargs):
            finished.append(kwargs)
        voice.store.finish = finish
        outcomes = await DuplexVoiceSession(voice, service, max_turns=1).run(
            blocks(voice, [1] * 16 + [0] * 12))
        assert len(outcomes) == 1 and outcomes[0].status == 'played'
        assert not finished and not voice.disconnected
    asyncio.run(run())


def test_terminal_stop_finishes_goodbye_audio_without_accepting_another_caller():
    class GoodbyeVoice(Voice):
        def __init__(self):
            super().__init__()
            self.state = CallState()
            self.goodbye_started, self.release = asyncio.Event(), asyncio.Event()
            self.goodbye_finished = False

        async def respond(self, final):
            self.state.lead_wants_out = True
            self.goodbye_started.set()
            await self.release.wait()
            self.epochs.require_current(final.turn_epoch)
            self.goodbye_finished = True
            return await super().respond(final)

    async def run():
        voice, service = GoodbyeVoice(), speech()
        async def source():
            i = 0
            for turn in range(2):
                if turn:
                    await voice.goodbye_started.wait()
                for value in [turn + 1] * 16 + [0] * 12:
                    i += 1
                    yield CaptureFrame(bytes([value, 0]) * 320, i * .02,
                                       voice.epochs.snapshot(), 0)
                    await asyncio.sleep(0)
            assert len(service.provider.requests) == 1
            assert not voice.interrupts and not voice.goodbye_finished
            voice.release.set()
            # Let playback finish before the capture task handles one more
            # ready frame; the result consumer must still report the goodbye.
            await asyncio.sleep(0)
            i += 1
            yield CaptureFrame(b'\0\0' * 320, i * .02, voice.epochs.snapshot(), 0)
        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert len(outcomes) == 1 and voice.goodbye_finished
        assert not voice.disconnected and voice.closed == 0
    asyncio.run(run())


def test_preterminal_session_closes_source_without_activating_capture_or_any_provider():
    async def run():
        voice, service, activated = Voice(), speech(), []
        voice.state = CallState(lead_wants_out=True)
        class Source:
            def __init__(self):
                self.closed = False
            def __aiter__(self):
                activated.append('source')
                return self
            async def __anext__(self):
                raise AssertionError('Terminal call cannot capture')
            async def aclose(self):
                self.closed = True
        source = Source()
        outcomes = await DuplexVoiceSession(voice, service,
            on_capture_started=lambda: activated.append('started')).run(source)
        assert outcomes == [] and source.closed and not activated
        assert not service.provider.requests and not voice.results
        assert not voice.disconnected and voice.closed == 0
    asyncio.run(run())


def test_real_voice_terminal_signoff_plays_fully_and_stops_later_asr():
    from tests.services.test_streaming_voice import StreamLLM, stream_voice

    class StopProvider(Provider):
        async def transcribe(self, request):
            self.requests.append(request)
            return STTResult('Bhai rehne do, baat nahi karni', 'hi', turn_epoch=request.turn_epoch)

    async def run():
        voice, service = stream_voice(StreamLLM(), minimum_speech_ms=200), speech(StopProvider())
        playing, release = asyncio.Event(), asyncio.Event()
        original = voice.speaker.play
        async def play(result):
            playing.set()
            await release.wait()
            await original(result)
        voice.speaker.play = play
        async def source():
            i = 0
            for turn in range(2):
                if turn:
                    await asyncio.wait_for(playing.wait(), 2)
                    assert voice.state.lead_wants_out
                for value in [1] * 16 + [0] * 12:
                    i += 1
                    yield CaptureFrame(bytes([value, 0]) * 320, i * .02,
                                       voice.epochs.snapshot(), 0)
                    await asyncio.sleep(0)
            assert len(service.provider.requests) == 1
            assert not voice.speaker.played
            release.set()
        outcomes = await DuplexVoiceSession(voice, service).run(source())
        assert len(outcomes) == 1 and outcomes[0].status == 'played'
        assert voice.speaker.played and not voice.disconnected and not voice._poisoned
        assert voice.attempt == 1
    asyncio.run(run())
