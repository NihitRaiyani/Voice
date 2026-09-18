"""G.711 μ-law codec (8kHz telephony), pure Python.

Twilio Media Streams carry 8kHz mono μ-law on the wire; Pipecat's pipeline works
in linear PCM16 and its TwilioFrameSerializer does the wire conversion. We still
need μ-law <-> PCM16 for two off-pipeline jobs: the offline WAV->clip converter
(`scripts/make_canned_clip.py`) and loading the committed .ulaw asset into PCM
frames (`canned.py`).

Implemented here rather than via stdlib `audioop` on purpose: `audioop` is removed
in Python 3.13, and this module is foundational. The algorithm is the standard
Sun/ITU-T G.711 μ-law; correctness is pinned by round-trip tests.
"""

_BIAS = 0x84
_CLIP = 32635


def _linear2ulaw(sample: int) -> int:
    """One PCM16 sample (signed int) -> one μ-law byte (0..255)."""
    sign = 0x80 if sample < 0 else 0x00
    if sign:
        sample = -sample
    if sample > _CLIP:
        sample = _CLIP
    sample += _BIAS
    exponent = max(0, min(7, sample.bit_length() - 8))
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _ulaw2linear(u: int) -> int:
    """One μ-law byte -> one PCM16 sample (signed int)."""
    u = ~u & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + _BIAS) << exponent
    sample -= _BIAS
    return -sample if sign else sample


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    """Little-endian signed PCM16 bytes -> μ-law bytes. Odd trailing byte dropped."""
    n = len(pcm) // 2
    out = bytearray(n)
    for i in range(n):
        lo = pcm[2 * i]
        hi = pcm[2 * i + 1]
        sample = (hi << 8) | lo
        if sample >= 0x8000:
            sample -= 0x10000
        out[i] = _linear2ulaw(sample)
    return bytes(out)


def ulaw_to_pcm16(ulaw: bytes) -> bytes:
    """μ-law bytes -> little-endian signed PCM16 bytes."""
    out = bytearray(len(ulaw) * 2)
    for i, u in enumerate(ulaw):
        sample = _ulaw2linear(u) & 0xFFFF
        out[2 * i] = sample & 0xFF
        out[2 * i + 1] = (sample >> 8) & 0xFF
    return bytes(out)


__all__ = ["pcm16_to_ulaw", "ulaw_to_pcm16"]
