"""Callback output owns PCM handoff; native abort never gates interruption."""
import asyncio
import threading
from time import perf_counter
from types import SimpleNamespace

import pytest
from roma.providers.audio.callback_output import CallbackOutput


def test_callback_handoff_and_epoch_clear_never_require_native_write_or_abort():
    generation = [0]
    output = CallbackOutput(generation=lambda: generation[0], current_epoch=lambda value: value == generation[0], clock=lambda: 10.0)
    assert output.enqueue(b"\1\0" * 320, generation=0, epoch=0)
    buffer = bytearray(640)
    output.callback(buffer, 320, SimpleNamespace(currentTime=100.0, outputBufferDacTime=100.02), SimpleNamespace(output_underflow=False))
    assert buffer == b"\1\0" * 320
    assert output.first_write_at == 10.0
    assert output.pop_receipt()[1] == pytest.approx(10.02)
    generation[0] = 1
    output.clear()
    buffer[:] = b"x" * 640
    output.callback(buffer, 320, SimpleNamespace(currentTime=100.02, outputBufferDacTime=100.04), SimpleNamespace(output_underflow=False))
    assert buffer == bytes(640)
    assert output.acknowledged_generation == 1


def test_capacity_and_native_underflow_fail_without_partial_handoff():
    output = CallbackOutput(generation=lambda: 0, current_epoch=lambda value: True, clock=lambda: 10.0)
    for _ in range(4):
        assert output.enqueue(b"\1\0" * 320, generation=0, epoch=0)
    assert not output.enqueue(b"\2\0" * 320, generation=0, epoch=0)
    buffer = bytearray(b"x" * 640)
    output.callback(buffer, 320, SimpleNamespace(currentTime=1.0, outputBufferDacTime=1.02), SimpleNamespace(output_underflow=True))
    assert buffer == bytes(640) and output.error == "Speaker underflow"
    assert output.pop_receipt() is None


class CallbackSpeakerBackend:
    """Native-like callback pumping, never a blocking write fallback."""
    def __init__(self, *, underflow=False, clock=perf_counter):
        self.events = []
        self.blocks = []
        self.underflow = underflow
        self.clock = clock
        self.created = 0
        self.writing = threading.Event()
        self.release = threading.Event()
        self.pump_enabled = threading.Event()
        self.pump_enabled.set()

    def RawOutputStream(self, **kwargs):
        owner = self
        self.created += 1
        assert kwargs["samplerate"] == 16000 and kwargs["blocksize"] == 320
        callback = kwargs["callback"]
        class Stream:
            latency = .02
            def __init__(self):
                self.closed = threading.Event()
                self.thread = None
            def start(self):
                owner.events.append("output_start")
                def pump():
                    while not self.closed.wait(.020):
                        if not owner.pump_enabled.is_set():
                            continue
                        data = bytearray(640)
                        now = owner.clock()
                        callback(data, 320, SimpleNamespace(currentTime=now, outputBufferDacTime=now + .02), SimpleNamespace(output_underflow=owner.underflow))
                        owner.blocks.append(bytes(data))
                        if any(data):
                            owner.writing.set()
                self.thread = threading.Thread(target=pump, daemon=True)
                self.thread.start()
            def abort(self):
                owner.events.append("output_abort")
                self.closed.set()
                self.thread.join(1)
                owner.release.set()
            def close(self):
                owner.events.append("output_close")
        return Stream()


def test_device_keeps_output_stream_and_clear_uses_callback_ack_without_native_abort():
    from roma.core.turn_epoch import TurnEpoch
    from roma.providers.ai.contracts import AudioFormat, TTSResult
    from roma.providers.audio.local_device import LocalAudioDevice

    async def run():
        backend, epochs = CallbackSpeakerBackend(), TurnEpoch()
        device = LocalAudioDevice(backend=backend, epoch_guard=epochs)
        task = asyncio.create_task(device.play(TTSResult(b"\1\0" * 8000, AudioFormat(8000), turn_epoch=0)))
        assert await asyncio.to_thread(backend.writing.wait, 1)
        assert device.playback_active and device.first_write_at is not None
        epochs.advance()
        await device.clear()
        assert not device.playback_active
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.events == ["output_start"]
        assert backend.blocks[-1] == bytes(640)
        await device.play(TTSResult(b"\2\0" * 160, AudioFormat(8000), turn_epoch=1))
        assert backend.created == 1 and not device.playback_active
        await device.aclose()
        assert backend.events == ["output_start", "output_abort", "output_close"]

    asyncio.run(run())


def test_epoch_change_during_callback_clock_read_drops_old_block_at_final_fence():
    generation, reads = [0], [0]
    def clock():
        reads[0] += 1
        if reads[0] == 1:
            generation[0] = 1
        return 10.0 if reads[0] == 1 else 10.5
    output = CallbackOutput(generation=lambda: generation[0], current_epoch=lambda epoch: epoch == generation[0], clock=clock)
    assert output.enqueue(b"\1\0" * 320, generation=0, epoch=0)
    buffer = bytearray(640)
    output.callback(buffer, 320, SimpleNamespace(currentTime=1., outputBufferDacTime=1.02), SimpleNamespace(output_underflow=False))
    assert buffer == bytes(640) and output.pop_receipt() is None
    assert output.stale_discarded == 1
    assert output.enqueue(b"\2\0" * 320, generation=1, epoch=1)
    output.callback(buffer, 320, SimpleNamespace(currentTime=1.02, outputBufferDacTime=1.04), SimpleNamespace(output_underflow=False))
    assert output.pop_receipt()[1] == pytest.approx(10.52)  # First actual handoff anchors.


def test_missing_output_callbacks_fail_clear_within_a_bounded_wait_without_native_abort():
    from roma.providers.ai.contracts import AudioFormat, TTSResult
    from roma.providers.audio.local_device import LocalAudioDevice

    async def run():
        backend = CallbackSpeakerBackend()
        device = LocalAudioDevice(backend=backend)
        await device.play(TTSResult(b"\1\0" * 320, AudioFormat(16000)))
        backend.pump_enabled.clear()
        started = perf_counter()
        with pytest.raises(RuntimeError, match="DEVICE-OUTPUT-CALLBACK-STALLED"):
            await device.clear()
        assert perf_counter() - started < .250
        assert backend.events == ["output_start"]
        assert device.capture_health["output_callback_stalls"] == 1
        backend.pump_enabled.set()
        await device.aclose()

    asyncio.run(run())


def test_actual_render_receipts_keep_sample_clock_despite_submillisecond_callback_jitter():
    from roma.providers.audio.local_device import LocalAudioDevice
    class Echo:
        def __init__(self):
            self.times = []
        def render(self, pcm, *, rate, at):
            self.times.append(at)
    echo = Echo()
    device = LocalAudioDevice(duplex=True, echo=echo)
    observations = iter([10., 10.0199])
    output = CallbackOutput(generation=lambda: 0, current_epoch=lambda value: True,
                            clock=lambda: next(observations))
    device._output_buffer = output
    for index in range(2):
        assert output.enqueue(bytes([index + 1, 0]) * 320, generation=0, epoch=0)
        output.callback(bytearray(640), 320,
                        SimpleNamespace(currentTime=100. + index * .020,
                                        outputBufferDacTime=100.02 + index * .020),
                        SimpleNamespace(output_underflow=False))
    device._clear_echo()
    assert echo.times == pytest.approx([10.02, 10.04])


def test_producer_stall_after_audio_handoff_fails_instead_of_silently_splicing_silence():
    output = CallbackOutput(generation=lambda: 0, current_epoch=lambda value: True, clock=lambda: 10.)
    output.begin_clip(generation=0, epoch=0)
    assert output.enqueue(b"\1\0" * 320, generation=0, epoch=0)
    buffer = bytearray(640)
    info, status = SimpleNamespace(currentTime=1., outputBufferDacTime=1.02), SimpleNamespace(output_underflow=False)
    output.callback(buffer, 320, info, status)
    output.callback(buffer, 320, info, status)
    assert output.error == "Output PCM queue starved"
    assert output.health()["output_queue_starvations"] == 1
    assert buffer == bytes(640)


def test_initial_silence_completed_clip_and_clear_are_not_producer_starvation():
    output = CallbackOutput(generation=lambda: 0, current_epoch=lambda value: True, clock=lambda: 10.)
    buffer = bytearray(640)
    info, status = SimpleNamespace(currentTime=1., outputBufferDacTime=1.02), SimpleNamespace(output_underflow=False)
    output.begin_clip(generation=0, epoch=0)
    output.callback(buffer, 320, info, status)
    assert output.error is None  # No audio handoff yet.
    assert output.enqueue(b"\1\0" * 320, generation=0, epoch=0)
    output.finish_clip()
    output.callback(buffer, 320, info, status)
    output.callback(buffer, 320, info, status)
    assert output.error is None  # Natural clip completion.
    output.begin_clip(generation=0, epoch=0)
    output.clear()
    output.callback(buffer, 320, info, status)
    assert output.error is None and output.starvations == 0


def test_cancelled_input_constructor_is_retained_joined_and_closed_once():
    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_persistent_capture import CallbackBackend

    async def run():
        entered, release, created = threading.Event(), threading.Event(), threading.Event()
        backend = CallbackBackend()
        factory = backend.RawInputStream
        def blocked_constructor(**kwargs):
            entered.set()
            assert release.wait(2)
            stream = factory(**kwargs)
            created.set()
            return stream
        backend.RawInputStream = blocked_constructor
        device = LocalAudioDevice(backend=backend)
        task = asyncio.create_task(device.start())
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(.010)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(created.wait, 1)
        await device.aclose()
        assert backend.events == ["abort", "close"]
        assert backend.created == 1
        with pytest.raises(RuntimeError):
            await device.start()

    asyncio.run(run())


def test_play_waits_for_final_callback_handoff_after_final_frame_has_been_popped():
    from roma.providers.ai.contracts import AudioFormat, TTSResult
    from roma.providers.audio.local_device import LocalAudioDevice

    async def run():
        entered, release = threading.Event(), threading.Event()
        callback_reads = [0]
        def clock():
            if "pump" in threading.current_thread().name:
                callback_reads[0] += 1
                if callback_reads[0] == 2:
                    entered.set()
                    assert release.wait(2)
            return perf_counter()
        backend = CallbackSpeakerBackend()
        device = LocalAudioDevice(backend=backend, clock=clock)
        task = asyncio.create_task(device.play(TTSResult(b"\1\0" * 640, AudioFormat(16000))))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(.080)  # Prior block's estimated DAC tail has ended.
            assert not task.done()  # Final block is popped, not yet handed off.
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await device.aclose()

    asyncio.run(run())


def test_reference_sample_clock_counts_silent_callbacks_and_ignores_wall_estimate_backstep():
    from roma.providers.audio.echo import RenderReference
    from roma.providers.audio.local_device import LocalAudioDevice

    observations = iter([10., 10.009, 10.080])
    output = CallbackOutput(generation=lambda: 0, current_epoch=lambda value: True,
                            clock=lambda: next(observations))
    reference = RenderReference(clock=lambda: 10.)
    device = LocalAudioDevice(duplex=True, echo=reference)
    device._output_buffer = output
    buffer = bytearray(640)
    status = SimpleNamespace(output_underflow=False)
    for index in range(4):
        if index != 2:  # A genuine silent native callback between utterances.
            assert output.enqueue(bytes([index + 1, 0]) * 320, generation=0, epoch=0)
        output.callback(buffer, 320,
                        SimpleNamespace(currentTime=100. + index * .020,
                                        outputBufferDacTime=100.02 + index * .020), status)
    receipts = list(output.receipts)
    device._clear_echo()  # Existing per-receipt wall estimates overlap here.
    assert [at for _, at in receipts] == pytest.approx([10.02, 10.04, 10.08])
    assert reference.read(at=10.04) == b"\2\0" * 320
    assert reference.read(at=10.06) == bytes(640)
    assert reference.read(at=10.08) == b"\4\0" * 320
    assert output.first_write_at == 10.
    assert output.health()['output_reference_clock_uncertainty_frames'] == 2
    assert output.health()['output_reference_clock_uncertainty_max_ms'] == pytest.approx(20.)
    assert output.error is None
