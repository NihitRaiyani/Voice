import struct
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest
from roma.domain.audio.resampling import PCMResampler
from roma.providers.audio.echo import (
    EchoReferenceOverflow,
    EchoUnavailable,
    RenderReference,
    WebRTCEchoCanceller,
)


def pcm(value, frames=320):
    return struct.pack(f"<{frames}h", *([value] * frames))


@pytest.fixture
def backend(monkeypatch):
    import importlib.metadata

    class Processor:
        def __init__(self, **options):
            self.options = options
            self.calls = []
            self.resets = 0

        def process(self, near, far):
            self.calls.append((near.copy(), far.copy()))
            return near.copy()

        def reset(self):
            self.resets += 1

    monkeypatch.setitem(
        sys.modules, "pywebrtc_audio", SimpleNamespace(AudioProcessor=Processor)
    )
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    return Processor


def test_render_reference_schedules_each_sample_in_audio_time():
    reference = RenderReference(clock=lambda: 1.0)
    reference.render(pcm(100, 3200), rate=16000, at=1.0)
    assert reference.read(at=0.98) == pcm(0)
    assert reference.read(at=1.0) == pcm(100)
    assert reference.read(at=1.18) == pcm(100)
    assert reference.read(at=1.2) == pcm(0)


def test_8khz_reference_preserves_resampler_history_across_native_writes():
    reference = RenderReference(clock=lambda: 1.0)
    samples = pcm(1000, 160) + pcm(-2000, 160)
    expected = PCMResampler(8000, 16000).feed(samples)
    reference.render(samples[:320], rate=8000, at=1.0)
    reference.render(samples[320:], rate=8000, at=1.02)
    assert reference.read(at=1.0) + reference.read(at=1.02) == expected


def test_reference_bounds_overflow_and_pruning_are_explicit():
    now = [1.0]
    reference = RenderReference(capacity_ms=400, clock=lambda: now[0])
    reference.render(pcm(1, 3200), rate=16000, at=0.8)
    reference.render(pcm(2, 3200), rate=16000, at=1.0)
    assert reference.pending_samples == 6400
    with pytest.raises(EchoReferenceOverflow):
        reference.render(pcm(3), rate=16000, at=1.0)
    assert reference.overflows == 1
    assert reference.pending_samples == 6400
    now[0] = 1.4
    assert reference.read(at=1.4) == pcm(0)
    assert reference.pending_samples == 0


@pytest.mark.parametrize(
    "rate,size,at",
    [
        (48000, 320, 1.0),
        (True, 320, 1.0),
        (16000, 0, 1.0),
        (16000, 3201, 1.0),
        (16000, 320, float("nan")),
        (16000, 320, float("inf")),
        (16000, 320, -1.0),
        (16000, 320, True),
        (16000, 320, 5.0),
    ],
)
def test_invalid_render_is_rejected_without_reference_mutation(rate, size, at):
    reference = RenderReference(clock=lambda: 1.0)
    with pytest.raises(ValueError):
        reference.render(pcm(1, size), rate=rate, at=at)
    assert reference.pending_samples == 0


def test_overlapping_render_is_rejected_without_replacing_reference():
    reference = RenderReference(clock=lambda: 1.0)
    reference.render(pcm(123), rate=16000, at=1.0)
    with pytest.raises(ValueError, match="overlap"):
        reference.render(pcm(456), rate=16000, at=1.01)
    assert reference.read(at=1.0) == pcm(123)


def test_interrupt_removes_future_audio_but_keeps_acoustic_tail_and_filter(backend):
    aec = WebRTCEchoCanceller(clock=lambda: 1.0)
    aec.preflight()
    processor = aec._processor
    aec.render(pcm(10), 16000, 1.0)
    aec.render(pcm(20), 16000, 1.02)
    aec.clear_future(1.03)
    assert aec.reference.read(at=1.0) == pcm(10)
    assert aec.reference.read(at=1.02) == pcm(20, 160) + pcm(0, 160)
    assert aec._processor is processor
    assert processor.resets == 0
    aec.render(pcm(30), 16000, 1.03)
    assert aec.reference.read(at=1.03) == pcm(30)


def test_missing_optional_dependency_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywebrtc_audio", None)
    aec = WebRTCEchoCanceller(clock=lambda: 1.0)
    with pytest.raises(EchoUnavailable, match="pinned duplex"):
        aec.preflight()
    with pytest.raises(EchoUnavailable):
        aec.process(pcm(123), 1.0)


def test_unpinned_native_version_fails_closed(backend, monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
    with pytest.raises(EchoUnavailable):
        WebRTCEchoCanceller(clock=lambda: 1.0).preflight()


@pytest.mark.parametrize("size", [0, 159, 160, 319, 321, 640])
def test_capture_requires_exactly_one_20ms_frame_before_loading_dsp(size, monkeypatch):
    monkeypatch.setitem(sys.modules, "pywebrtc_audio", None)
    with pytest.raises(ValueError, match="20 ms"):
        WebRTCEchoCanceller(clock=lambda: 1.0).process(pcm(1, size), 1.0)


def test_capture_uses_two_10ms_native_calls_with_exact_time_matched_far_end(backend):
    aec = WebRTCEchoCanceller(clock=lambda: 1.0)
    aec.render(pcm(10, 160) + pcm(20, 160), 16000, 1.0)
    near = pcm(100, 160) + pcm(200, 160)
    assert aec.process(near, 1.0) == near
    calls = aec._processor.calls
    assert len(calls) == 2
    for index, (capture, far) in enumerate(calls):
        assert capture.dtype.name == far.dtype.name == "int16"
        assert capture.tolist() == [100 * (index + 1)] * 160
        assert far.tolist() == [10 * (index + 1)] * 160
    assert aec._processor.options == {
        "sample_rate": 16000,
        "echo_cancellation": True,
        "noise_suppression": False,
        "auto_gain_control": False,
        "stream_delay_ms": 40,
    }


def test_real_capture_gap_resets_filter_and_backwards_capture_is_rejected(backend):
    aec = WebRTCEchoCanceller(clock=lambda: 1.0)
    aec.process(pcm(1), 1.0)
    aec.process(pcm(2), 1.04)
    assert aec.discontinuities == 1
    assert aec._processor.resets == 1
    with pytest.raises(ValueError, match="backwards"):
        aec.process(pcm(3), 1.02)


def test_malformed_native_output_fails_closed(backend, monkeypatch):
    monkeypatch.setattr(backend, "process", lambda *args: b"bad")
    with pytest.raises(EchoUnavailable):
        WebRTCEchoCanceller(clock=lambda: 1.0).process(pcm(123), 1.0)


@pytest.mark.parametrize("failure", [ValueError, OSError, RuntimeError])
def test_native_failure_is_sanitized_without_raw_capture_fallback(
    backend, monkeypatch, failure
):
    def fail(*args):
        raise failure("unreviewed native diagnostics")

    monkeypatch.setattr(backend, "process", fail)
    with pytest.raises(EchoUnavailable) as caught:
        WebRTCEchoCanceller(clock=lambda: 1.0).process(pcm(123), 1.0)
    assert "unreviewed" not in str(caught.value)


def test_noise_suppression_can_be_explicitly_enabled(backend):
    aec = WebRTCEchoCanceller(noise_suppression=True, clock=lambda: 1.0)
    aec.preflight()
    assert aec._processor.options["noise_suppression"] is True


def test_concurrent_capture_fails_without_waiting_for_native_dsp(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    process = backend.process

    def blocked(self, near, far):
        entered.set()
        assert release.wait(2)
        return process(self, near, far)

    monkeypatch.setattr(backend, "process", blocked)
    aec = WebRTCEchoCanceller(clock=lambda: 1.0)
    aec.preflight()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(aec.process, pcm(123), 1.0)
        assert entered.wait(1)
        second = pool.submit(aec.process, pcm(456), 1.02)
        try:
            with pytest.raises(EchoUnavailable, match="serial"):
                second.result(timeout=0.2)
        except TimeoutError:
            pytest.fail("A concurrent capture waited for native DSP instead of failing closed")
        finally:
            release.set()
        assert first.result(timeout=1) == pcm(123)


def test_small_capture_clock_jitter_preserves_adaptation_and_reference_alignment(backend):
    aec = WebRTCEchoCanceller(clock=lambda: 1.0)
    aec.render(pcm(10), 16000, 1.0)
    aec.render(pcm(20), 16000, 1.02)
    assert aec.process(pcm(100), 1.0) == pcm(100)
    assert aec.process(pcm(200), 1.021) == pcm(200)
    assert aec.discontinuities == 0
    assert aec._processor.resets == 0
    assert aec._processor.calls[2][1].tolist() == [20] * 160


@pytest.fixture
def native_backend():
    import importlib.metadata

    native = pytest.importorskip("pywebrtc_audio", reason="install the pinned duplex extra")
    if importlib.metadata.version("pywebrtc-audio") != "0.2.0":
        pytest.skip("synthetic native evidence requires the pinned duplex extra")
    return native


@pytest.mark.parametrize(
    "kind,minimum_erle,minimum_near_energy",
    [
        ("voiced", 5.0, 0.5),
        ("noise", 15.0, 0.25),
    ],
)
def test_real_dsp_attenuates_echo_and_retains_independent_near_end(
    native_backend, kind, minimum_erle, minimum_near_energy
):
    from roma.eval.echo import synthetic_probe

    result = synthetic_probe(far_kind=kind)
    assert result["late_4_to_6s_erle_db"] > minimum_erle
    assert result["double_talk_8_to_12s"]["energy_ratio"] > minimum_near_energy
    assert result["double_talk_8_to_12s"]["lag_aligned_correlation"] > 0.5
    assert result["near_only_14_to_16s"]["energy_ratio"] > 0.7
    assert result["capture_discontinuities"] == result["reference_overflows"] == 0


def test_native_backend_releases_gil_while_processing(native_backend):
    import numpy as np

    processor = native_backend.AudioProcessor(
        sample_rate=16000, echo_cancellation=True, auto_gain_control=False
    )
    samples = np.zeros(640000, dtype=np.int16)
    entered, finished = threading.Event(), threading.Event()

    def process():
        entered.set()
        result = processor.process(samples, samples)
        finished.set()
        return result

    # With a long interpreter switch interval, the waiting Python thread only
    # resumes inside process() if native processing releases the GIL. Use an
    # independent native instance here; production always feeds bounded 10 ms.
    previous_interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(1.0)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process)
            assert entered.wait(2)
            assert not finished.is_set(), "Native DSP held the GIL until completion"
            assert future.result(timeout=5).shape == samples.shape
    finally:
        sys.setswitchinterval(previous_interval)


def test_explicit_render_coverage_distinguishes_known_silence_from_missing_future(backend):
    aec = WebRTCEchoCanceller(clock=lambda: 1.)
    aec.require_reference_coverage()
    aec.reference.advance_through(1.02)
    assert aec.process(pcm(100), 1.) == pcm(100)
    assert aec.reference.known_silent_samples == 320
    with pytest.raises(EchoUnavailable, match='reference'):
        aec.process(pcm(200), 1.02)
    assert aec.reference.unknown_frames == 1


def test_known_initial_silence_is_valid_but_pruned_history_is_unknown(backend):
    now = [1.]
    aec = WebRTCEchoCanceller(clock=lambda: now[0])
    aec.require_reference_coverage()
    aec.reference.advance_through(1.02)
    assert aec.process(pcm(100), .98) == pcm(100)
    now[0] = 3.
    aec.reference.advance_through(3.02)
    with pytest.raises(EchoUnavailable, match='reference'):
        aec.reference.read(at=1., require_known=True)
    assert aec.reference.unknown_samples == 320
