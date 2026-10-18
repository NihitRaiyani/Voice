import math

import pytest
from roma.domain.audio import g711
from roma.domain.audio.pcm import AudioFormatError, PCMFramer, pack, unpack
from roma.domain.audio.resampling import PCMResampler
from roma.domain.audio.session import TelephoneEgress


def tone(rate, frequency, count, amplitude=10000):
    return pack(
        [round(amplitude * math.sin(2 * math.pi * frequency * i / rate)) for i in range(count)]
    )


def rms(pcm):
    samples = unpack(pcm)
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def test_known_g711_silence_sign_and_full_scale_vectors():
    assert unpack(g711.decode(bytes([255, 127, 0, 128, 254, 126]))) == (
        0,
        0,
        -32124,
        32124,
        8,
        -8,
    )
    assert g711.encode(pack([0, 32767, -32768])) == bytes([255, 128, 0])
    with pytest.raises(AudioFormatError):
        g711.encode(b"\x00")


@pytest.mark.parametrize("source,target", [(8000, 16000), (16000, 8000), (8000, 8000)])
def test_streaming_resampling_is_chunk_invariant_even_at_odd_sample_boundaries(source, target):
    data = tone(source, 1000, 701)
    whole = PCMResampler(source, target)
    expected = whole.feed(data) + whole.finish()
    stream = PCMResampler(source, target)
    result = (
        b"".join(stream.feed(data[i : i + 14]) for i in range(0, len(data), 14))
        + stream.finish()
    )
    assert result == expected
    assert stream.finish() == b""
    with pytest.raises(AudioFormatError):
        stream.feed(pack([1]))
    stream.reset()
    assert stream.feed(data) == PCMResampler(source, target).feed(data)


def test_downsampling_rejects_aliasing_instead_of_dropping_every_second_sample():
    def converted(freq):
        return PCMResampler(16000, 8000).feed(tone(16000, freq, 4096))[256:]

    passed, rejected = rms(converted(1000)), rms(converted(6000))
    assert 6500 < passed < 7600
    assert rejected < passed / 100  # >=40 dB rejection at 6 kHz


def test_upsampling_suppresses_the_7khz_image_of_a_1khz_input():
    samples = unpack(PCMResampler(8000, 16000).feed(tone(8000, 1000, 4096)))[128:]

    def magnitude(freq):
        return abs(
            sum(
                s
                * complex(
                    math.cos(2 * math.pi * freq * i / 16000),
                    math.sin(2 * math.pi * freq * i / 16000),
                )
                for i, s in enumerate(samples)
            )
        )

    assert magnitude(7000) < magnitude(1000) / 100


def test_mulaw_resampling_round_trip_retains_duration_and_signal_quality():
    original = tone(8000, 1000, 4096)
    up = PCMResampler(8000, 16000).feed(g711.decode(g711.encode(original)))
    down = PCMResampler(16000, 8000).feed(up)
    assert len(up) == 2 * len(original) and len(down) == len(original)
    # Two 31-sample delays at 16 kHz equal 31 samples at 8 kHz.
    reference = unpack(original)[128:-128]
    actual = unpack(down)[128 + 31 : -128 + 31]
    error = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(reference, actual, strict=True)) / len(reference)
    )
    assert error < 150  # >33 dB SNR against the ~7,071 RMS test signal


def test_pcm_frames_require_alignment_and_explicit_tail_policy():
    framer = PCMFramer(16000, max_frames=3)
    assert framer.feed(pack([100] * 100)) == ()
    assert len(framer.feed(pack([100] * 220))[0]) == 640
    with pytest.raises(AudioFormatError):
        framer.feed(b"\x00")
    with pytest.raises(AudioFormatError):
        framer.feed(pack([0] * 1000))
    framer.feed(pack([100]))
    with pytest.raises(AudioFormatError):
        framer.finish()
    assert len(framer.finish(pad=True)) == 640
    assert framer.finish(pad=True) == b""


def test_egress_frames_are_160_bytes_and_interruption_drops_old_filter_state():
    sender = TelephoneEgress()
    frames = sender.feed(tone(16000, 1000, 960)) + sender.finish()
    assert frames and all(len(frame) == 160 for frame in frames)
    sender.reset()
    sender.feed(tone(16000, 1000, 100))
    sender.reset()
    assert sender.feed(pack([0] * 320)) == (bytes([255]) * 160,)
    assert TelephoneEgress().finish() == ()


@pytest.mark.parametrize("source,target", [(44100, 8000), (8000, 24000)])
def test_unsupported_rates_fail_explicitly(source, target):
    with pytest.raises(AudioFormatError):
        PCMResampler(source, target)


def test_all_mulaw_decode_codes_match_the_independent_stdlib_reference():
    audioop = pytest.importorskip("audioop")
    payload = bytes(range(256))
    assert g711.decode(payload) == audioop.ulaw2lin(payload, 2)
