from hashlib import sha256

from roma.domain.audio.adaptive_jitter import AdaptiveJitterBuffer, PacketEnvelope
from roma.domain.audio.pcm import pack


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def packet(seq, value=1000, *, epoch=0):
    payload = pack([value] * 320)
    return PacketEnvelope(seq, (seq - 1) * 20, epoch, sha256(payload).hexdigest()), payload


def test_reorders_pcm_and_timer_releases_the_last_frame_without_another_arrival():
    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    envelope, pcm = packet(1)
    assert buffer.push(envelope, pcm)
    assert buffer.drain() == ()
    clock.now = 10
    assert buffer.push(*packet(3, 3000))
    clock.now = 20
    assert buffer.push(*packet(2, 2000))
    clock.now = 140
    output = buffer.drain()
    assert [frame.seq_no for frame in output] == [1, 2, 3]
    assert [frame.pcm for frame in output] == [packet(n, n * 1000)[1] for n in (1, 2, 3)]
    assert all(not frame.concealed for frame in output)
    assert buffer.pending == 0


def test_transit_variation_uses_rfc3550_ewma_and_bounds_the_adaptive_target():
    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    assert buffer.push(*packet(1))
    clock.now = 60
    assert buffer.push(*packet(2))
    assert buffer.jitter_ms == 2.5
    clock.now = 90
    assert buffer.push(*packet(3))
    assert buffer.jitter_ms == 2.96875
    assert 40 <= buffer.target_delay_ms <= 120


def test_admission_rejects_bad_hash_timestamp_duplicates_and_overflow_then_recovers():
    from dataclasses import replace

    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock, max_packets=3)
    envelope, pcm = packet(1)
    assert not buffer.push(envelope, bytes(640))
    assert buffer.push(envelope, pcm)
    assert not buffer.push(envelope, pcm)
    bad, payload = packet(2)
    assert not buffer.push(replace(bad, timestamp_ms=21), payload)
    assert buffer.push(*packet(2)) and buffer.push(*packet(3))
    assert not buffer.push(*packet(4))
    clock.now = 120
    assert len(buffer.drain()) == 3
    assert not buffer.push(*packet(1))
    assert buffer.push(*packet(4))
    clock.now = 160
    assert [frame.seq_no for frame in buffer.drain()] == [4]
    assert buffer.metrics.invalid == 2
    assert buffer.metrics.duplicates == 1 and buffer.metrics.overflow == 1
    assert buffer.metrics.late == 1 and buffer.metrics.max_depth <= 3


def test_four_lost_frames_conceals_only_three_then_marks_a_discontinuity():
    from roma.domain.audio.pcm import unpack

    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    buffer.push(*packet(1))
    clock.now = 1
    buffer.push(*packet(6))
    clock.now = 300
    output = buffer.drain()
    assert [frame.seq_no for frame in output] == [1, 2, 3, 4, 5, 6]
    concealed = [frame for frame in output if frame.concealed]
    assert len(concealed) == 3
    assert [unpack(frame.pcm)[0] for frame in concealed] == [750, 562, 421]
    assert output[4].pcm is None and output[4].missing_frames == 1
    assert buffer.metrics.gap_frames == 4 and buffer.metrics.discontinuities == 1
    assert buffer.metrics.interpolated_frames == 3


def test_explicit_tail_flush_and_new_epoch_discard_stale_pending_audio():
    import pytest
    from roma.domain.audio.pcm import AudioFormatError

    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    assert buffer.push(*packet(1))
    out = buffer.flush(last_seq_no=3)
    assert [frame.seq_no for frame in out] == [1, 2, 3]
    assert [frame.concealed for frame in out] == [False, True, True]
    assert buffer.flush() == ()
    buffer.advance_epoch(1)
    assert not buffer.push(*packet(4, epoch=0))
    assert buffer.push(*packet(1, epoch=1))
    buffer.advance_epoch(2)
    assert buffer.pending == 0 and buffer.metrics.discarded == 1
    assert buffer.push(*packet(1, 3000, epoch=2))
    assert buffer.flush()[0].pcm == packet(1, 3000)[1]
    assert buffer.metrics.stale_epoch == 1
    with pytest.raises(AudioFormatError):
        buffer.advance_epoch(2)


def test_far_sequence_is_rejected_and_large_permitted_gap_has_bounded_output_work():
    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    assert buffer.push(*packet(1))
    assert not buffer.push(*packet(100000))
    assert buffer.push(*packet(100))
    output = buffer.flush()
    assert len(output) == 6
    assert output[-1].seq_no == 100
    assert buffer.metrics.gap_frames == 98
    assert buffer.metrics.concealed_frames == 3
    assert buffer.pending == 0


def test_clock_and_metadata_reject_bool_nonfinite_and_nonmonotonic_input():
    import pytest
    from roma.domain.audio.pcm import AudioFormatError

    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    buffer.push(*packet(1))
    for bad in (True, float('nan'), float('inf'), -1):
        clock.now = bad
        with pytest.raises(AudioFormatError):
            buffer.drain()
    with pytest.raises(AudioFormatError):
        PacketEnvelope(True, 0, 0, '0' * 64)
    with pytest.raises(AudioFormatError):
        PacketEnvelope.from_dict({'seq_no': 1, 'timestamp_ms': 0, 'turn_epoch': 0,
                                  'payload_hash': '0' * 64, 'caller_text': 'forbidden'})


def test_plc_interpolates_toward_known_future_waveform_and_budget_spans_timer_drains():
    from roma.domain.audio.pcm import unpack

    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    buffer.push(*packet(1, 1000))
    buffer.push(*packet(3, 3000))
    middle = buffer.flush()[1]
    assert middle.concealed and unpack(middle.pcm)[0] == 1500
    buffer = AdaptiveJitterBuffer(clock=clock)
    buffer.push(*packet(1))
    buffer.push(*packet(6))
    output = []
    for tick in (60, 80, 100, 120, 140, 160):
        clock.now = tick
        output.extend(buffer.drain())
    assert sum(frame.concealed for frame in output) == 3
    assert sum(frame.pcm is None for frame in output) == 1
    assert output[-1].seq_no == 6


def test_closed_tail_cannot_be_extended_to_invent_more_audio_without_a_new_epoch():
    import pytest
    from roma.domain.audio.pcm import AudioFormatError

    clock = Clock()
    buffer = AdaptiveJitterBuffer(clock=clock)
    buffer.push(*packet(1))
    buffer.flush()
    with pytest.raises(AudioFormatError):
        buffer.flush(last_seq_no=10)
