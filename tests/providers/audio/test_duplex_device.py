"""Persistent full-duplex device tests use fake native streams and injected DSP."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from roma.providers.ai.contracts import AudioFormat, TTSResult
from roma.providers.audio.local_device import LocalAudioDevice
from tests.providers.audio.test_callback_output import CallbackSpeakerBackend
from tests.providers.audio.test_persistent_capture import CallbackBackend


class Echo:
    def __init__(self):
        self.processed, self.rendered, self.cuts = [], [], []
    def preflight(self):
        pass
    def process(self, pcm, *, at):
        self.processed.append((pcm, at))
        return bytes([pcm[0] + 1, 0]) * 320
    def render(self, pcm, rate, *, at):
        self.rendered.append((pcm, rate, at))
    def clear_future(self, *, at=None):
        self.cuts.append(at)


class Backend(CallbackBackend):
    def __init__(self):
        super().__init__()
        self.output = CallbackSpeakerBackend()
        self.writing, self.release = self.output.writing, self.output.release

    def RawOutputStream(self, **kwargs):
        return self.output.RawOutputStream(**kwargs)


def test_capture_keeps_processing_during_playback_and_output_clear():
    async def run():
        backend, echo = Backend(), Echo()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        await device.start()
        source = device.frames(max_seconds=2, on_capture_started=lambda: backend.emit(1))
        playback = None
        try:
            first = await anext(source)
            assert first.pcm == b"\2\0" * 320
            playback = asyncio.create_task(device.play(TTSResult(b"\5\0" * 8000, AudioFormat(8000))))
            assert await asyncio.to_thread(backend.writing.wait, 1)
            assert device.capture_mode == "ACTIVE"
            backend.emit(2)
            second = await asyncio.wait_for(anext(source), 1)
            assert second.pcm == b"\3\0" * 320 and len(echo.processed) == 2
            assert echo.rendered and len(echo.rendered[0][0]) == 640  # 20ms16k
            await device.clear()
            with pytest.raises(asyncio.CancelledError):
                await playback
            assert device.capture_mode == "ACTIVE"
            assert backend.output.events == ["output_start"]
            backend.emit(3)
            third = await anext(source)
            assert third.pcm == b"\4\0" * 320
        finally:
            backend.release.set()
            if playback is not None:
                await asyncio.gather(playback, return_exceptions=True)
            await source.aclose()
            await device.aclose()
        assert backend.created == 1 and backend.events.count("close") == 1
    asyncio.run(run())


def test_adc_frame_clock_ignores_callback_jitter_and_epochs_do_not_mute_capture():
    from roma.core.turn_epoch import TurnEpoch

    async def run():
        now, guard = [10.0], TurnEpoch()
        backend, echo = Backend(), Echo()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo,
                                  epoch_guard=guard, clock=lambda: now[0])
        await device.start()
        source = device.frames(max_seconds=2)
        pending = asyncio.create_task(anext(source))
        await asyncio.sleep(0)
        try:
            backend.callback(b"\1\0" * 320, 320,
                             SimpleNamespace(inputBufferAdcTime=100.0, currentTime=100.02),
                             SimpleNamespace(input_overflow=False))
            first = await pending
            now[0] += 0.025  # callback scheduling jitter; ADC remains contiguous.
            guard.advance()
            backend.callback(b"\2\0" * 320, 320,
                             SimpleNamespace(inputBufferAdcTime=100.02, currentTime=100.04),
                             SimpleNamespace(input_overflow=False))
            second = await anext(source)
            assert second.at - first.at == pytest.approx(0.020)
            assert (first.epoch, second.epoch) == (0, 1)
            assert device.capture_mode == "ACTIVE"
        finally:
            await source.aclose()
            await device.aclose()
    asyncio.run(run())


def test_actual_adc_gap_rejects_incomplete_input_instead_of_stitching_it():
    async def run():
        backend, echo = Backend(), Echo()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        await device.start()
        source = device.frames(max_seconds=2)
        pending = asyncio.create_task(anext(source))
        await asyncio.sleep(0)
        try:
            backend.callback(b"\1\0" * 320, 320,
                             SimpleNamespace(inputBufferAdcTime=100.0, currentTime=100.02),
                             SimpleNamespace(input_overflow=False))
            await pending
            backend.callback(b"\2\0" * 320, 320,
                             SimpleNamespace(inputBufferAdcTime=100.06, currentTime=100.08),
                             SimpleNamespace(input_overflow=False))
            with pytest.raises(ValueError, match="overflow"):
                await anext(source)
        finally:
            await source.aclose()
            await device.aclose()
    asyncio.run(run())


def test_discontinuity_recovery_flushes_and_reuses_the_same_native_input():
    from roma.core.turn_epoch import TurnEpoch

    async def run():
        backend, echo, guard = Backend(), Echo(), TurnEpoch()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo, epoch_guard=guard)
        recovered = []

        async def recover():
            assert device.capture_mode == 'MUTED_HALF_DUPLEX'
            recovered.append(guard.advance())
            backend.emit(9)  # input arriving during cleanup is discarded.
            asyncio.get_running_loop().call_soon(backend.emit, 2)

        await device.start()
        source = device.frames(max_seconds=2, on_discontinuity=recover,
                               on_capture_started=lambda: backend.emit(1, overflow=True))
        try:
            frame = await asyncio.wait_for(anext(source), 1)
            assert frame.pcm == b'\3\0' * 320
            assert (frame.generation, frame.epoch) == (2, 1)
            assert recovered == [1] and backend.created == 1
            assert device.capture_mode == 'ACTIVE'
        finally:
            await source.aclose()
            await device.aclose()
        assert backend.events.count('close') == 1

    asyncio.run(run())


def test_discontinuity_recovery_stops_after_three_attempts():
    from roma.providers.audio.capture_buffer import CaptureDiscontinuity

    async def run():
        backend, echo = Backend(), Echo()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        retries = []

        async def recover():
            retries.append(True)
            asyncio.get_running_loop().call_soon(lambda: backend.emit(1, overflow=True))

        await device.start()
        source = device.frames(max_seconds=2, on_discontinuity=recover,
                               on_capture_started=lambda: backend.emit(1, overflow=True))
        try:
            with pytest.raises(CaptureDiscontinuity):
                await asyncio.wait_for(anext(source), 1)
            assert len(retries) == 3 and backend.created == 1
            assert device.capture_mode == 'MUTED_HALF_DUPLEX'
        finally:
            await source.aclose()
            await device.aclose()

    asyncio.run(run())


def test_cancelled_discontinuity_recovery_leaves_capture_muted():
    async def run():
        backend, echo, entered = Backend(), Echo(), asyncio.Event()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)

        async def recover():
            entered.set()
            await asyncio.Event().wait()

        await device.start()
        source = device.frames(max_seconds=2, on_discontinuity=recover,
                               on_capture_started=lambda: backend.emit(1, overflow=True))
        pending = asyncio.create_task(anext(source))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert device.capture_mode == 'MUTED_HALF_DUPLEX' and backend.created == 1
            backend.emit(9)
            assert not device.capture._frames
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await source.aclose()
            await device.aclose()

    asyncio.run(run())


def test_recovery_never_refreshes_original_capture_deadline():
    async def run():
        from roma.core.voice_errors import VoiceErrorCode, VoiceFailure

        backend, echo, now = Backend(), Echo(), [10.0]
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo, clock=lambda: now[0])

        async def recover():
            now[0] = 12.0

        await device.start()
        source = device.frames(max_seconds=1, on_discontinuity=recover,
                               on_capture_started=lambda: backend.emit(1, overflow=True))
        try:
            with pytest.raises(VoiceFailure) as error:
                await anext(source)
            assert error.value.code == VoiceErrorCode.CAPTURE
            assert device.capture_mode == 'MUTED_HALF_DUPLEX' and backend.created == 1
        finally:
            await source.aclose()
            await device.aclose()

    asyncio.run(run())


def test_invalid_aec_result_never_enters_capture_recovery():
    class InvalidEcho(Echo):
        def process(self, pcm, *, at):
            return b''

    async def run():
        backend = Backend()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=InvalidEcho())

        async def recover():
            pytest.fail('AEC errors must remain terminal')

        await device.start()
        source = device.frames(max_seconds=2, on_discontinuity=recover,
                               on_capture_started=lambda: backend.emit(1))
        try:
            with pytest.raises(ValueError, match='Echo processing'):
                await anext(source)
            assert device.capture_mode == 'MUTED_HALF_DUPLEX'
        finally:
            await source.aclose()
            await device.aclose()

    asyncio.run(run())


def test_clear_during_receipt_render_preserves_handed_audio_reference():
    class PausedEcho(Echo):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()

        def render(self, pcm, rate, *, at):
            self.entered.set()
            assert self.release.wait(2)
            super().render(pcm, rate, at=at)

    async def run():
        backend, echo = Backend(), PausedEcho()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        await device.start()
        source = device.frames(max_seconds=2, on_capture_started=lambda: backend.emit(1))
        await anext(source)
        playback = asyncio.create_task(device.play(TTSResult(b'\5\0' * 8000, AudioFormat(8000))))
        clearing = capture = None
        try:
            assert await asyncio.to_thread(echo.entered.wait, 1)
            assert any(any(block) for block in backend.output.blocks)
            clearing = asyncio.create_task(device.clear())
            backend.emit(2)
            capture = asyncio.create_task(anext(source))
            echo.release.set()
            await asyncio.wait_for(capture, 1)
            await asyncio.wait_for(clearing, 1)
            await asyncio.gather(playback, return_exceptions=True)
            # References represent actual callback handoffs, including the DAC
            # tail still audible after generation clear; no speculative PCM.
            assert echo.rendered and all(len(pcm) == 640 for pcm, _, _ in echo.rendered)
            assert not echo.cuts
            assert backend.output.events == ["output_start"]
        finally:
            echo.release.set()
            for task in (playback, clearing, capture):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (playback, clearing, capture) if task is not None),
                                 return_exceptions=True)
            await source.aclose()
            await device.aclose()
    asyncio.run(run())


def test_echo_receipt_failure_still_allows_native_output_close():
    class BrokenEcho(Echo):
        def render(self, pcm, rate, *, at):
            raise ValueError('DSP reference failed')

    async def run():
        backend, echo = Backend(), BrokenEcho()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        await device.start()
        try:
            with pytest.raises(ValueError, match='DSP reference failed'):
                await device.play(TTSResult(b'\5\0' * 8000, AudioFormat(8000)))
            assert backend.output.writing.is_set()
        finally:
            await device.aclose()
        assert backend.output.events == ["output_start", "output_abort", "output_close"]
        assert backend.created == 1 and backend.events.count('close') == 1
    asyncio.run(run())


def test_device_to_coordinator_barge_keeps_capture_and_caller_onset():
    from roma.services.duplex_voice import DuplexVoiceSession
    from tests.services.test_duplex_voice import Voice, speech

    class CleanEcho(Echo):
        def process(self, pcm, *, at):
            self.processed.append((pcm, at))
            return pcm

    async def run():
        backend, echo = Backend(), CleanEcho()
        # Pace both fake input and persistent speaker callbacks at 20 ms so
        # native handoff/watchdog time and capture time use the same clock.
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        class PlayingVoice(Voice):
            async def respond(self, final):
                if final.turn_epoch == 1:
                    await device.play(TTSResult(b'\5\0' * 8000, AudioFormat(8000),
                                                turn_epoch=final.turn_epoch))
                return await super().respond(final)
            async def interrupt(self, *, owner=None, turn_epoch=None):
                self.epochs.require_current(turn_epoch)
                self.interrupts.append(turn_epoch)
                owner.cancel()
                await device.clear()
        voice = PlayingVoice()
        voice.clock, voice._started = device.clock, device.clock()
        voice.speaker = device
        device.bind_epoch_guard(voice.epochs)
        service = speech()
        await device.start()
        opened = asyncio.Event()
        source = device.frames(max_seconds=5, on_capture_started=opened.set)
        async def feed():
            await opened.wait()
            for value in [1] * 12 + [0] * 12:
                backend.emit(value)
                await asyncio.sleep(.020)
            assert await asyncio.to_thread(backend.writing.wait, 1)
            assert device.capture_mode == 'ACTIVE'
            for value in [2] * 16 + [0] * 12:
                backend.emit(value)
                await asyncio.sleep(.020)
        feeding = asyncio.create_task(feed())
        try:
            outcomes = await asyncio.wait_for(
                DuplexVoiceSession(voice, service, max_turns=1).run(source), 3)
            await feeding
            assert len(outcomes) == 1 and voice.interrupts == [2]
            assert len(service.provider.requests) == 2
            assert service.provider.requests[1].audio.count(b'\2\0') == 16 * 320
            assert backend.output.events == ["output_start"]
            assert sum(pcm == b'\2\0' * 320 for pcm, _ in echo.processed) == 16
        finally:
            backend.release.set()
            feeding.cancel()
            await asyncio.gather(feeding, return_exceptions=True)
            await source.aclose()
            await device.aclose()
        assert backend.created == 1 and backend.events.count('close') == 1
    asyncio.run(run())


def test_epoch_only_invalidation_discards_queued_pcm_without_creating_reference():
    from roma.core.turn_epoch import TurnEpoch
    from roma.providers.audio.callback_output import CallbackOutput

    guard, echo = TurnEpoch(), Echo()
    device = LocalAudioDevice(duplex=True, echo=echo, epoch_guard=guard)
    output = device._output_buffer = CallbackOutput(
        generation=lambda: device._output_generation,
        current_epoch=guard.is_current, clock=device.clock)
    assert output.enqueue(b'\5\0' * 320, generation=0, epoch=0)
    guard.advance()
    buffer = bytearray(640)
    output.callback(buffer, 320, SimpleNamespace(currentTime=10, outputBufferDacTime=10.02),
                    SimpleNamespace(output_underflow=False))
    device._process_echo(b'\2\0' * 320, device.clock())
    assert buffer == bytes(640)
    assert not echo.rendered and output.stale_discarded == 1


def test_epoch_advance_after_callback_preserves_actual_handoff_reference_for_capture():
    from roma.core.turn_epoch import TurnEpoch
    from roma.providers.audio.callback_output import CallbackOutput

    guard, echo = TurnEpoch(), Echo()
    device = LocalAudioDevice(duplex=True, echo=echo, epoch_guard=guard)
    output = device._output_buffer = CallbackOutput(
        generation=lambda: device._output_generation,
        current_epoch=guard.is_current, clock=device.clock)
    assert output.enqueue(b'\5\0' * 320, generation=0, epoch=0)
    buffer = bytearray(640)
    output.callback(buffer, 320, SimpleNamespace(currentTime=10, outputBufferDacTime=10.02),
                    SimpleNamespace(output_underflow=False))
    guard.advance()
    device._process_echo(b'\2\0' * 320, device.clock())
    assert buffer == b'\5\0' * 320
    assert echo.rendered and echo.rendered[0][0] == buffer
    assert not echo.cuts
