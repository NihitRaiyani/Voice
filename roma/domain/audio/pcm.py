"""Mono signed little-endian PCM16 and exact-duration frame boundaries."""

import struct


class AudioFormatError(ValueError):
    pass


def validate_pcm16(pcm: bytes) -> None:
    if len(pcm) % 2:
        raise AudioFormatError("PCM16 must contain complete two-byte samples")


def unpack(pcm: bytes) -> tuple[int, ...]:
    validate_pcm16(pcm)
    return struct.unpack(f"<{len(pcm) // 2}h", pcm)


def pack(samples) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


class PCMFramer:
    """Strict 20 ms mono frames; bounded input/carry and explicit final-tail policy."""

    def __init__(self, sample_rate: int, *, max_frames: int = 50) -> None:
        if sample_rate not in (8000, 16000) or not 1 <= max_frames <= 100:
            raise AudioFormatError("Expected 8/16 kHz and a finite frame bound")
        self.frame_bytes = sample_rate // 50 * 2
        self.max_bytes = self.frame_bytes * max_frames
        self._carry = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._carry)

    def feed(self, pcm: bytes) -> tuple[bytes, ...]:
        validate_pcm16(pcm)
        if len(self._carry) + len(pcm) > self.max_bytes:
            raise AudioFormatError("PCM input exceeds the bounded framing batch")
        self._carry.extend(pcm)
        frames = []
        while len(self._carry) >= self.frame_bytes:
            frames.append(bytes(self._carry[: self.frame_bytes]))
            del self._carry[: self.frame_bytes]
        return tuple(frames)

    def finish(self, *, pad: bool = False) -> bytes:
        if self._carry and not pad:
            raise AudioFormatError("Partial final frame; explicitly pad or discard it")
        tail = bytes(self._carry)
        self._carry.clear()
        return tail.ljust(self.frame_bytes, b"\x00") if tail else b""

    def discard(self) -> int:
        size = len(self._carry)
        self._carry.clear()
        return size
