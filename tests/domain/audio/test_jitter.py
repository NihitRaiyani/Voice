import pytest
from roma.domain.audio.jitter import AudioPacket, FixedJitterBuffer
from roma.domain.audio.pcm import AudioFormatError
from roma.domain.audio.session import TelephoneIngress


def packet(n, payload=None):
    return AudioPacket(n, (n - 1) * 20, payload if payload is not None else bytes([255]) * 160)


@pytest.mark.parametrize("delay", [40, 50, 60])
def test_fixed_startup_delay_and_timer_releases_last_frame_without_next_arrival(delay):
    stream = TelephoneIngress(delay_ms=delay)
    assert stream.receive(packet(1), now_ms=0)
    assert stream.drain(now_ms=delay - 1) == ()
    output = stream.drain(now_ms=delay)
    assert len(output) == 1 and output[0].pcm == bytes(640)
    assert stream.jitter.pending == 0


def test_reorder_and_duplicate_are_resolved_before_decode():
    buffer = FixedJitterBuffer()
    assert buffer.push(packet(1), now_ms=0)
    assert buffer.push(packet(3), now_ms=20)
    assert buffer.push(packet(2), now_ms=30)
    assert not buffer.push(packet(2), now_ms=30)
    assert [item.sequence for item in buffer.drain(now_ms=100)] == [1, 2, 3]
    assert buffer.metrics.reordered == 1 and buffer.metrics.duplicates == 1
    assert not buffer.push(packet(1), now_ms=101)
    assert buffer.metrics.late == 1


def test_missing_frame_is_a_discontinuity_without_plc_or_half_samples():
    stream = TelephoneIngress()
    stream.receive(packet(1), now_ms=0)
    stream.receive(packet(3), now_ms=40)
    out = stream.drain(now_ms=100)
    assert [frame.sequence for frame in out] == [1, 3]
    assert [frame.discontinuity for frame in out] == [False, True]
    assert stream.metrics()["sequence_gaps"] == 1
    assert all(len(frame.pcm) == 640 for frame in out)


def test_large_lost_run_resumes_with_bounded_gap_work():
    buffer = FixedJitterBuffer()
    buffer.push(packet(1), now_ms=0)
    buffer.drain(now_ms=60)
    assert buffer.push(packet(100000), now_ms=2000000)
    out = buffer.drain(now_ms=2000100)
    assert len(out) == 2 and out[0].missing_frames == 99998
    assert out[1].sequence == 100000
    assert buffer.metrics.sequence_gaps == 99998


def test_queue_and_format_validation_are_bounded():
    buffer = FixedJitterBuffer(max_packets=3)
    for n in (1, 2, 3):
        assert buffer.push(packet(n), now_ms=0)
    assert not buffer.push(packet(4), now_ms=0)
    assert buffer.metrics.overflow == 1
    assert buffer.discard() == 3 and buffer.pending == 0
    assert not buffer.push(packet(4, b"\xff"), now_ms=0)
    assert not buffer.push(AudioPacket(4, 61, bytes(160)), now_ms=0)
    assert buffer.metrics.invalid == 2
    with pytest.raises(AudioFormatError):
        buffer.drain(now_ms=-1)
    with pytest.raises(AudioFormatError):
        FixedJitterBuffer(delay_ms=100)


def test_two_calls_do_not_share_queue_filter_or_metrics():
    first, second = TelephoneIngress(), TelephoneIngress()
    first.receive(packet(1), now_ms=0)
    assert first.drain(now_ms=60) and second.drain(now_ms=60) == ()
    assert first.metrics()["decoded_frames"] == 1 and second.metrics()["decoded_frames"] == 0


def test_reordered_first_packet_does_not_erase_earlier_packets_or_start_early():
    buffer = FixedJitterBuffer()
    assert buffer.push(packet(2), now_ms=0)
    assert buffer.push(packet(1), now_ms=10)
    assert buffer.drain(now_ms=59) == ()
    assert [frame.sequence for frame in buffer.drain(now_ms=80)] == [1, 2]
    assert buffer.metrics.sequence_gaps == 0


@pytest.mark.parametrize("payload", ["x" * 160, [255] * 160, None, bytearray(160)])
def test_invalid_payload_type_is_rejected_before_anchor_or_decode(payload):
    stream = TelephoneIngress()
    assert not stream.receive(AudioPacket(1, 0, payload), now_ms=0)
    assert stream.jitter.pending == 0 and stream.jitter.expected == 1
    assert stream.metrics()["invalid"] == 1
    assert stream.receive(packet(1), now_ms=1)
    frames = stream.drain(now_ms=61)
    assert len(frames) == 1 and frames[0].pcm == bytes(640)
