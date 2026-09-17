import struct

import pytest

from roma.telephony.ulaw import pcm16_to_ulaw, ulaw_to_pcm16


def _pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def test_ulaw_bytes_one_per_sample():
    pcm = _pcm([0, 100, -100, 30000, -30000])
    assert len(pcm16_to_ulaw(pcm)) == 5


def test_decode_doubles_length():
    assert len(ulaw_to_pcm16(bytes([0xFF, 0x7F]))) == 4


def test_roundtrip_is_stable_after_first_quantization():
    pcm = _pcm(list(range(-32768, 32768, 137)))
    u1 = pcm16_to_ulaw(pcm)
    u2 = pcm16_to_ulaw(ulaw_to_pcm16(u1))
    assert u1 == u2


def test_roundtrip_error_within_mulaw_tolerance():
    pcm = _pcm([0, 500, -500, 8000, -8000, 32000, -32000])
    back = struct.unpack(f"<{len(pcm) // 2}h", ulaw_to_pcm16(pcm16_to_ulaw(pcm)))
    orig = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    for o, b in zip(orig, back, strict=True):
        assert abs(o - b) <= max(64, abs(o) * 0.10)


def test_odd_trailing_byte_dropped():
    assert len(pcm16_to_ulaw(b"\x01\x02\x03")) == 1


def test_matches_audioop_if_available():
    audioop = pytest.importorskip("audioop")
    pcm = _pcm([0, 1234, -1234, 20000, -20000, 32767, -32768])
    assert pcm16_to_ulaw(pcm) == audioop.lin2ulaw(pcm, 2)
