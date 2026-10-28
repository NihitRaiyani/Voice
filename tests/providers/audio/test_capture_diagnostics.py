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
        try:
            with pytest.raises(ValueError, match='underflow'):
                await audio.play(TTSResult(b'\0\0' * 160, AudioFormat(8000)))
            assert audio.capture_health['portaudio_output_underflows'] == 1
            assert audio.capture_health['active_capture_losses'] == 0
        finally:
            with pytest.raises(ValueError, match='underflow'):
                await audio.aclose()

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


@pytest.mark.parametrize("rows", [
    [(10.0, 100.0, 100.02), (10.05, 0, 0), (10.06, 100.04, 100.06)],
    [(10.0, 100.0, 100.02), (10.02, 100.027, 100.04), (10.04, 100.04, 100.06)],
    [(10.0, 100.0, 100.02), (10.02, 100.013, 100.04), (10.04, 100.04, 100.06)],
    [(10.0, 0, 0), (10.05, 100.02, 100.04), (10.06, 0, 0)],
])
def test_complete_pcm_survives_adc_source_change_and_transient_resampling_jitter(rows):
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.0]
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    received = []
    for index, (wall, adc, current) in enumerate(rows, 1):
        now[0] = wall
        pcm = bytes([index, 0]) * 320
        device._capture_callback(pcm, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=current),
                                 SimpleNamespace(input_overflow=False))
        received.append(device.capture.pop(generation))
        assert received[-1].pcm == pcm
    assert [frame.at for frame in received] == pytest.approx([10.0, 10.02, 10.04])
    assert device.capture_health['portaudio_input_overflows'] == 0
    assert device.capture_health['pcm_sequence_gaps'] == 0
    assert device.capture_health['active_capture_losses'] == 0
    assert device.capture_health['capture_adc_valid_timestamps'] == sum(adc > 0 for _, adc, _ in rows)
    assert device.capture_health['capture_adc_invalid_timestamps'] == sum(adc == 0 for _, adc, _ in rows)
    for example in device.capture_health['capture_adc_anomaly_examples']:
        if example['expected_adc_delta_ms'] != 20:
            assert example['current_clock_delta_error_ms'] is None
            assert example['adc_current_delta_error_ms'] is None


@pytest.mark.parametrize("error", [.020, .040, -.020, .300])
def test_full_frame_adc_disagreement_remains_fail_closed(error):
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.0]
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    device._capture_deadline = 10.10
    for adc in [100.0, 100.02 + error]:
        device._capture_callback(b'\1\0' * 320, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=adc + .02),
                                 SimpleNamespace(input_overflow=False))
    with pytest.raises(CaptureDiscontinuity):
        device.capture.pop(generation)
    assert device.capture_health['pcm_sequence_gaps'] == 1
    assert device.capture_health['portaudio_input_overflows'] == 0


def test_persistent_subframe_uncertainty_preserves_pcm_but_native_overflow_still_rejects_input():
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: 10.0)
    generation = device.capture.activate(0, follow_epochs=True)
    for adc in [100.0, 100.027, 100.047, 100.067]:
        device._capture_callback(b'\1\0' * 320, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=adc + .02),
                                 SimpleNamespace(input_overflow=False))
        assert device.capture.pop(generation) is not None
    assert device.capture_health['capture_adc_timestamp_anomalies'] == 3
    assert device.capture_health['capture_adc_subframe_uncertainty_frames'] == 3
    assert device.capture_health['pcm_sequence_gaps'] == 0
    generation = device.capture.activate(1, follow_epochs=True)
    device._capture_callback(b'\2\0' * 320, 320, SimpleNamespace(),
                             SimpleNamespace(input_overflow=True))
    with pytest.raises(CaptureDiscontinuity):
        device.capture.pop(generation)
    assert device.capture_health['portaudio_input_overflows'] == 1


def test_adc_diagnostics_are_relative_bounded_detached_and_track_slow_clock_drift():
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.0]
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    previous = None
    for index in range(2000):
        now[0] = 10 + index * .020
        adc = 100 + index * .02002  # Synthetic ADC/device clock drift, no missing PCM.
        device._capture_callback(b'\1\0' * 320, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=adc + .02),
                                 SimpleNamespace(input_overflow=False))
        frame = device.capture.pop(generation)
        if previous is not None:
            assert .01995 <= frame.at - previous <= .02005
        previous = frame.at
    health = device.capture_health
    assert health['pcm_sequence_gaps'] == 0
    assert 0 < health['capture_adc_calibration_max_ms'] <= .05
    current = adc + .020
    for index in range(32):
        now[0] += .020
        current += .020
        adc += .020 + (.007 if index % 2 == 0 else -.007)
        device._capture_callback(b'\2\0' * 320, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=current),
                                 SimpleNamespace(input_overflow=False))
        assert device.capture.pop(generation) is not None
    health = device.capture_health
    assert len(health['capture_adc_anomaly_examples']) == 8
    assert health['capture_adc_timestamp_anomalies'] == 16
    assert health['capture_adc_valid_timestamps'] == 2032
    assert health['capture_adc_invalid_timestamps'] == 0
    example = health['capture_adc_anomaly_examples'][0]
    assert example['adc_delta_ms'] == pytest.approx(27)
    assert example['expected_adc_delta_ms'] == 20
    assert 'adc_time_s' not in example and 'current_time_s' not in example
    example['phase_error_ms'] = 999
    assert device.capture_health['capture_adc_anomaly_examples'][0]['phase_error_ms'] != 999
    json.dumps(health, allow_nan=False)


@pytest.mark.parametrize("deltas", [
    [(67.916667, 67.777667, 26.099833), (20, 20, 20), (20, 20, 20)],
    [(27.250, 27.483292, 25.513625), (20, 19.953458, 14.949958),
     (20, 19.982542, 19.974250)],
])
def test_observed_native_clock_domain_steps_preserve_complete_pcm(deltas):
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.0]
    adc, current = 100.0, 100.02
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    received = []
    for index, (adc_delta, current_delta, wall_delta) in enumerate([(0, 0, 0), *deltas]):
        adc += adc_delta / 1000
        current += current_delta / 1000
        now[0] += wall_delta / 1000
        pcm = bytes([index + 1, 0]) * 320
        device._capture_callback(pcm, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=current),
                                 SimpleNamespace(input_overflow=False))
        received.append(device.capture.pop(generation))
        assert received[-1].pcm == pcm
    assert [frame.at for frame in received] == pytest.approx([10 + i * .020 for i in range(4)])
    health = device.capture_health
    assert health['capture_adc_clock_rebases'] == 1
    assert health['pcm_sequence_gaps'] == health['active_capture_losses'] == 0
    assert health['portaudio_input_overflows'] == 0
    example = next(row for row in health['capture_adc_anomaly_examples'] if row['clock_rebased'])
    if deltas[0][0] > 60:
        assert example['current_clock_delta_error_ms'] == pytest.approx(41.677834)
        assert example['clock_rebase_ms'] == pytest.approx(47.916667)
    else:
        assert example['current_clock_delta_error_ms'] == pytest.approx(5.0035)
        assert example['clock_rebase_ms'] == pytest.approx(7.25)
    json.dumps(health, allow_nan=False)


@pytest.mark.parametrize("adc_delta,current_delta,wall_delta", [
    (60, 60, 60),  # Actual full-frame callback gap: no independent clock jump.
    (60, 20, 20),  # ADC gap alone: hardware clocks do not agree.
    (60, 60, 80),  # Opposite-sign current/perf discrepancy and large callback gap.
    (60, 60, 1),  # Catch-up callback burst has no reliable wall-clock corroboration.
    (140, 140, 20),  # Common-domain step exceeds the 100ms rebase bound.
])
def test_clock_rebase_does_not_hide_missing_frames_or_unbounded_steps(adc_delta, current_delta, wall_delta):
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.0]
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    device._capture_callback(b'\1\0' * 320, 320,
                             SimpleNamespace(inputBufferAdcTime=100, currentTime=100.10),
                             SimpleNamespace(input_overflow=False))
    assert device.capture.pop(generation) is not None
    now[0] += wall_delta / 1000
    device._capture_callback(b'\2\0' * 320, 320,
                             SimpleNamespace(inputBufferAdcTime=100 + adc_delta / 1000,
                                             currentTime=100.10 + current_delta / 1000),
                             SimpleNamespace(input_overflow=False))
    with pytest.raises(CaptureDiscontinuity):
        device.capture.pop(generation)
    health = device.capture_health
    assert health['capture_adc_clock_rebases'] == 0
    assert health['pcm_sequence_gaps'] == 1


@pytest.mark.parametrize("fault", ['native', 'invalid', 'capacity'])
def test_clock_domain_evidence_never_masks_authoritative_capture_loss(fault):
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.0]
    device = LocalAudioDevice(duplex=True, echo=Echo(), capture_capacity=1, clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    device._capture_callback(b'\1\0' * 320, 320,
                             SimpleNamespace(inputBufferAdcTime=100, currentTime=100.02),
                             SimpleNamespace(input_overflow=False))
    if fault != 'capacity':
        assert device.capture.pop(generation) is not None
    now[0] += .026099833
    device._capture_callback(b'\2\0' * (319 if fault == 'invalid' else 320), 320,
                             SimpleNamespace(inputBufferAdcTime=100.067916667, currentTime=100.087777667),
                             SimpleNamespace(input_overflow=fault == 'native'))
    with pytest.raises(CaptureDiscontinuity):
        device.capture.pop(generation)
    counter = {'native': 'portaudio_input_overflows', 'invalid': 'invalid_input_blocks',
               'capacity': 'ring_buffer_capacity_drops'}[fault]
    assert device.capture_health[counter] == 1


def test_observed_stable_subframe_adc_phase_is_uncertainty_not_repeated_pcm_loss():
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now, adc, current = [10.], 100., 100.03
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    received = []
    deltas = [(0, 0, 0), (30.0825, 23.339166, 20.777458),
              (19.999958, 19.962792, 20.066333), (20., 20., 20.)]
    for index, (adc_delta, current_delta, wall_delta) in enumerate(deltas, 1):
        adc += adc_delta / 1000
        current += current_delta / 1000
        now[0] += wall_delta / 1000
        pcm = bytes([index, 0]) * 320
        device._capture_callback(pcm, 320,
                                 SimpleNamespace(inputBufferAdcTime=adc, currentTime=current),
                                 SimpleNamespace(input_overflow=False))
        frame = device.capture.pop(generation)
        assert frame.pcm == pcm
        received.append(frame)
    assert [frame.at for frame in received] == pytest.approx([9.99, 10.01, 10.03, 10.05])
    health = device.capture_health
    assert health['pcm_sequence_gaps'] == health['active_capture_losses'] == 0
    assert health['capture_adc_clock_rebases'] == 0
    assert health['capture_adc_subframe_uncertainty_frames'] == 3
    assert health['capture_adc_subframe_uncertainty_max_ms'] == pytest.approx(10.0825)


@pytest.mark.parametrize('phase_step', [.007, -.007])
def test_cumulative_subframe_adc_disagreements_still_fail_at_one_complete_frame(phase_step):
    from types import SimpleNamespace

    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.providers.audio.test_duplex_device import Echo

    now = [10.]
    device = LocalAudioDevice(duplex=True, echo=Echo(), clock=lambda: now[0])
    generation = device.capture.activate(0, follow_epochs=True)
    for index in range(4):
        now[0] = 10. + index * .020
        device._capture_callback(b"\1\0" * 320, 320,
            SimpleNamespace(inputBufferAdcTime=100. + index * (.020 + phase_step),
                            currentTime=100.10 + index * .020),
            SimpleNamespace(input_overflow=False))
        if index < 3:
            assert device.capture.pop(generation) is not None
    with pytest.raises(CaptureDiscontinuity):
        device.capture.pop(generation)
    health = device.capture_health
    assert health['capture_adc_subframe_uncertainty_frames'] == 2
    assert health['capture_adc_subframe_uncertainty_max_ms'] == pytest.approx(14.)
    assert health['capture_adc_clock_rebases'] == 0
    assert health['pcm_sequence_gaps'] == health['active_capture_losses'] == 1
