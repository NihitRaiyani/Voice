"""Metadata-only capture pressure diagnostics use synthetic PCM, never hardware."""

import json

import pytest
from roma.providers.audio.capture_buffer import CaptureBuffer, CaptureDiscontinuity


def test_buffer_health_distinguishes_capacity_loss_from_native_input_loss():
    buffer = CaptureBuffer(2)
    generation = buffer.activate(0)
    buffer.push(b'\1\0' * 320, 320, 0)
    buffer.push(b'\2\0' * 320, 320, 0)
    before = buffer.health()
    assert before['ring_buffer_high_watermark_frames'] == 2
    assert before['ring_buffer_occupancy_pct'] == 100
    buffer.push(b'\3\0' * 320, 320, 0)
    with pytest.raises(CaptureDiscontinuity):
        buffer.pop(generation)
    health = buffer.health()
    assert health['ring_buffer_capacity_drops'] == 1
    assert health['callback_lock_contentions'] == health['invalid_input_blocks'] == 0
    assert health['ring_buffer_capacity_frames'] == 2
    assert health['ring_buffer_occupancy_frames'] == 0
    assert before['ring_buffer_occupancy_frames'] == 2  # detached snapshot
    buffer.activate(1)
    buffer.push(b'\4\0' * 320, 320, 1, overflow=True)
    assert buffer.health()['ring_buffer_capacity_drops'] == 1
    json.dumps(health)


def test_device_health_separates_callback_jitter_adc_gaps_and_native_flags():
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.0]
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    device.capture.activate(0, follow_epochs=True)
    for wall, adc, overflow in [(10.0, 100.0, False), (10.025, 100.02, False),
                                (10.070, 100.08, False), (10.090, 100.10, True)]:
        now[0] = wall
        device._capture_callback(b'\1\0' * 320, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=adc + .02),
                                 SimpleNamespace(input_overflow=overflow))
    health = device.capture_health
    assert health['scope'] == 'device_lifetime'
    assert health['audio_callback_count'] == 4
    assert health['audio_cb_interval_max_us'] == pytest.approx(45000)
    assert health['portaudio_input_overflows'] == 1
    assert health['pcm_sequence_gaps'] == 1
    assert health['portaudio_output_underflows'] == 0
    assert health['active_capture_losses'] == 2
    assert health['ring_buffer_high_watermark_frames'] == 2
    json.dumps(health)


def test_capture_health_measures_loop_stall_and_stops_monitoring_on_close():
    import asyncio
    import time

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_persistent_capture import CallbackBackend

    def stall_loop():
        time.sleep(.05)  # The measured fault injection is intentionally synchronous.

    async def run():
        device = LocalAudioDevice(backend=CallbackBackend())
        await device.start()
        try:
            await asyncio.sleep(.025)
            stall_loop()
            await asyncio.sleep(.025)
            health = device.capture_health
            assert health['asyncio_loop_stall_max_ms'] >= 30
            assert health['asyncio_loop_heartbeat_samples'] > 0
        finally:
            await device.aclose()
        samples = device.capture_health['asyncio_loop_heartbeat_samples']
        await asyncio.sleep(.025)
        assert device.capture_health['asyncio_loop_heartbeat_samples'] == samples

    asyncio.run(run())


def test_delayed_consumer_health_does_not_discard_or_retimestamp_audio():
    import asyncio

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_persistent_capture import CallbackBackend, Speech

    async def run():
        now = [10.0]
        backend, speech = CallbackBackend(), Speech()
        device = LocalAudioDevice(backend=backend, clock=lambda: now[0])
        await device.start()

        def opened():
            backend.emit(1)
            now[0] += .040

        try:
            result, _ = await device.listen(speech, on_capture_started=opened)
            assert result.text == 'synthetic'
            assert speech.received == [b'\1\0' * 320]
            assert device.capture_health['capture_frame_age_max_ms'] == pytest.approx(40)
            assert device.capture_health['capture_recoveries'] == 0
        finally:
            await device.aclose()

    asyncio.run(run())


def test_invalid_input_and_callback_contention_have_distinct_loss_counters():
    buffer = CaptureBuffer(2)
    generation = buffer.activate(0)
    buffer.push(b'\1\0' * 10, 10, 0)
    with pytest.raises(CaptureDiscontinuity):
        buffer.pop(generation)
    assert buffer.health()['invalid_input_blocks'] == 1
    buffer.activate(1)
    with buffer._lock:
        buffer.push(b'\1\0' * 320, 320, 1)
        snapshot = buffer.health()  # Diagnostics must not acquire this callback lock.
    assert snapshot['callback_lock_contentions'] == 1
    assert snapshot['ring_buffer_capacity_drops'] == 0


def test_output_underflow_is_reported_as_native_status_without_capture_loss():
    import asyncio

    from roma.providers.ai.contracts import AudioFormat, TTSResult
    from tests.providers.audio.test_local_device import Stream, device

    async def run():
        audio = device(Stream(underflow=True))
        with pytest.raises(ValueError, match='underflow'):
            await audio.play(TTSResult(b'\0\0' * 160, AudioFormat(8000)))
        assert audio.capture_health['portaudio_output_underflows'] == 1
        assert audio.capture_health['active_capture_losses'] == 0

    asyncio.run(run())


def test_recovery_counter_survives_generation_reset_and_keeps_input_open():
    import asyncio

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Backend, Echo

    async def run():
        backend = Backend()
        device = LocalAudioDevice(backend=backend, duplex=True, echo=Echo())

        async def recover():
            asyncio.get_running_loop().call_soon(backend.emit, 1)

        await device.start()
        source = device.frames(max_seconds=2, on_discontinuity=recover,
                               on_capture_started=lambda: backend.emit(1, overflow=True))
        try:
            await asyncio.wait_for(anext(source), 1)
            health = device.capture_health
            assert health['capture_recoveries'] == health['portaudio_input_overflows'] == 1
            assert health['active_capture_losses'] == 1
            assert backend.created == 1
        finally:
            await source.aclose()
            await device.aclose()

    asyncio.run(run())


def test_buffer_health_distinguishes_current_occupancy_from_lifetime_high_watermark():
    buffer = CaptureBuffer(2)
    generation = buffer.activate(0)
    buffer.push(b'\1\0' * 320, 320, 0)
    buffer.push(b'\2\0' * 320, 320, 0)
    assert buffer.pop(generation) is not None
    partial = buffer.health()
    assert partial['ring_buffer_occupancy_pct'] == 50
    assert partial['ring_buffer_high_watermark_pct'] == 100
    buffer.mute()
    muted = buffer.health()
    assert muted['ring_buffer_occupancy_pct'] == 0
    assert muted['ring_buffer_high_watermark_pct'] == 100
    assert partial['ring_buffer_occupancy_pct'] == 50
