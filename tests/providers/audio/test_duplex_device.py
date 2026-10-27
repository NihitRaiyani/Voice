"""Persistent full-duplex device tests use fake native streams and injected DSP."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from roma.providers.ai.contracts import AudioFormat, TTSResult
from roma.providers.audio.local_device import LocalAudioDevice
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
        self.writing, self.release = threading.Event(), threading.Event()
    def RawOutputStream(self, **kwargs):
        owner = self
        class Stream:
            latency = 0.02
            def start(self):
                pass
            def write(self, pcm):
                owner.writing.set()
                owner.release.wait(2)
                return False
            def abort(self):
                owner.release.set()
            def stop(self):
                pass
            def close(self):
                pass
        return Stream()


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
            playback = asyncio.create_task(device.play(TTSResult(b"\5\0" * 320, AudioFormat(8000))))
            assert await asyncio.to_thread(backend.writing.wait, 1)
            assert device.capture_mode == "ACTIVE"
            backend.emit(2)
            second = await asyncio.wait_for(anext(source), 1)
            assert second.pcm == b"\3\0" * 320 and len(echo.processed) == 2
            assert echo.rendered and len(echo.rendered[0][0]) == 320  # 20ms8k
            await device.clear()
            with pytest.raises(asyncio.CancelledError):
                await playback
            assert device.capture_mode == "ACTIVE" and echo.cuts
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


def test_clear_during_render_never_exposes_unplayed_reference_to_capture():
    class PausedEcho(Echo):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()
            self.unplayed = False
            self.polluted = False
        def render(self, pcm, rate, *, at):
            self.entered.set()
            assert self.release.wait(2)
            self.unplayed = True
        def clear_future(self, *, at=None):
            self.unplayed = False
            super().clear_future(at=at)
        def process(self, pcm, *, at):
            self.polluted |= self.unplayed
            return super().process(pcm, at=at)

    async def run():
        backend, echo = Backend(), PausedEcho()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        await device.start()
        source = device.frames(max_seconds=2, on_capture_started=lambda: backend.emit(1))
        await anext(source)
        playback = asyncio.create_task(device.play(TTSResult(b'\5\0' * 160, AudioFormat(8000))))
        assert await asyncio.to_thread(echo.entered.wait, 1)
        clearing = asyncio.create_task(device.clear())
        await asyncio.sleep(0.01)
        backend.emit(2)
        capture = asyncio.create_task(anext(source))
        echo.release.set()
        await asyncio.wait_for(capture, 1)
        await asyncio.wait_for(clearing, 1)
        await asyncio.gather(playback, return_exceptions=True)
        try:
            assert not echo.polluted
            assert not backend.writing.is_set()
        finally:
            await source.aclose()
            await device.aclose()
    asyncio.run(run())


def test_echo_cleanup_failure_still_aborts_and_closes_native_output():
    class BrokenEcho(Echo):
        broken = False
        def clear_future(self, *, at=None):
            if self.broken:
                raise ValueError('DSP cleanup failed')
            super().clear_future(at=at)

    async def run():
        backend, echo = Backend(), BrokenEcho()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo)
        await device.start()
        playback = asyncio.create_task(device.play(TTSResult(b'\5\0' * 160, AudioFormat(8000))))
        assert await asyncio.to_thread(backend.writing.wait, 1)
        echo.broken = True
        try:
            with pytest.raises(ValueError, match='DSP cleanup failed'):
                await device.clear()
            assert backend.release.is_set()
            await asyncio.gather(playback, return_exceptions=True)
            assert device._output is None
        finally:
            echo.broken = False
            backend.release.set()
            await asyncio.gather(playback, return_exceptions=True)
            await device.aclose()
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
        input_frames = [0]

        def clock():
            return 10 + input_frames[0] / 50

        # Each callback contains 20 ms of PCM. Device and coordinator must share
        # that sample clock even when callbacks arrive in bounded fast bursts;
        # wall-clock pacing would otherwise put fake ADC timestamps in the future.
        device = LocalAudioDevice(backend=backend, duplex=True, echo=echo, clock=clock)
        class PlayingVoice(Voice):
            async def respond(self, final):
                if final.turn_epoch == 1:
                    await device.play(TTSResult(b'\5\0' * 160, AudioFormat(8000),
                                                turn_epoch=final.turn_epoch))
                return await super().respond(final)
            async def interrupt(self, *, owner=None, turn_epoch=None):
                self.epochs.require_current(turn_epoch)
                self.interrupts.append(turn_epoch)
                owner.cancel()
                await device.clear()
        voice = PlayingVoice()
        voice.clock, voice._started = clock, clock()
        voice.speaker = device
        device.bind_epoch_guard(voice.epochs)
        service = speech()
        await device.start()
        opened = asyncio.Event()
        source = device.frames(max_seconds=5, on_capture_started=opened.set)
        async def feed():
            await opened.wait()
            for value in [1] * 12 + [0] * 12:
                input_frames[0] += 1
                backend.emit(value)
                await asyncio.sleep(0)
            assert await asyncio.to_thread(backend.writing.wait, 1)
            assert device.capture_mode == 'ACTIVE'
            for value in [2] * 16 + [0] * 12:
                input_frames[0] += 1
                backend.emit(value)
                await asyncio.sleep(0)
        feeding = asyncio.create_task(feed())
        try:
            outcomes = await asyncio.wait_for(
                DuplexVoiceSession(voice, service, max_turns=1).run(source), 3)
            await feeding
            assert len(outcomes) == 1 and voice.interrupts == [2]
            assert len(service.provider.requests) == 2
            assert service.provider.requests[1].audio.count(b'\2\0') == 16 * 320
            assert backend.release.is_set()
            assert sum(pcm == b'\2\0' * 320 for pcm, _ in echo.processed) == 16
        finally:
            backend.release.set()
            feeding.cancel()
            await asyncio.gather(feeding, return_exceptions=True)
            await source.aclose()
            await device.aclose()
        assert backend.created == 1 and backend.events.count('close') == 1
    asyncio.run(run())


def test_epoch_only_invalidation_scrubs_unplayed_render_before_capture():
    from roma.core.turn_epoch import TurnEpoch
    guard = TurnEpoch()
    class EpochEcho(Echo):
        unplayed = False
        polluted = False
        def render(self, pcm, rate, *, at):
            self.unplayed = True
            guard.advance()
        def clear_future(self, *, at=None):
            self.unplayed = False
        def process(self, pcm, *, at):
            self.polluted |= self.unplayed
            return pcm
    echo = EpochEcho()
    device = LocalAudioDevice(duplex=True, echo=echo, epoch_guard=guard)
    with pytest.raises(asyncio.CancelledError):
        device._render_echo(b'\5\0' * 160, SimpleNamespace(latency=.02), 8000, 0, 0)
    device._process_echo(b'\2\0' * 320, device.clock())
    assert not echo.polluted


def test_epoch_advance_after_render_scrubs_future_reference_before_capture():
    from roma.core.turn_epoch import TurnEpoch
    guard, echo = TurnEpoch(), Echo()
    device = LocalAudioDevice(duplex=True, echo=echo, epoch_guard=guard)
    device._render_echo(b'\5\0' * 160, SimpleNamespace(latency=.02), 8000, 0, 0)
    guard.advance()
    device._process_echo(b'\2\0' * 320, device.clock())
    assert echo.cuts
