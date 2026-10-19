"""Fixed-delay playout for normalized 20 ms packets, driven by an injected clock.

Reorder before decoding, skip gaps explicitly, and never synthesize PLC. A timer
must call drain() even when no next packet arrives. Raw RTP parsing/wraparound and
adaptive buffering belong to the gated advanced track.
"""

import math
from dataclasses import asdict, dataclass

from roma.domain.audio.pcm import AudioFormatError


@dataclass(frozen=True)
class AudioPacket:
    sequence: int
    timestamp_ms: int
    payload: bytes


@dataclass(frozen=True)
class Playout:
    sequence: int
    timestamp_ms: int
    payload: bytes | None  # None is a discontinuity, not synthetic silence.
    missing_frames: int = 0


@dataclass
class JitterMetrics:
    received: int = 0
    emitted: int = 0
    sequence_gaps: int = 0
    gap_events: int = 0
    duplicates: int = 0
    late: int = 0
    reordered: int = 0
    invalid: int = 0
    overflow: int = 0
    max_depth: int = 0
    max_wait_ms: float = 0
    discarded: int = 0

    def snapshot(self):
        return asdict(self)


class FixedJitterBuffer:
    def __init__(
        self, *, delay_ms: int = 60, max_packets: int = 8, first_sequence: int = 1
    ) -> None:
        if not 40 <= delay_ms <= 60 or not 3 <= max_packets <= 50 or first_sequence < 0:
            raise AudioFormatError("Expected 40–60 ms delay and a bounded packet queue")
        self.delay_ms, self.max_packets = delay_ms, max_packets
        self.expected = first_sequence
        self.metrics = JitterMetrics()
        self._queue: dict[int, tuple[AudioPacket, float]] = {}
        self._anchor: tuple[int, int, float] | None = None
        self._highest = first_sequence - 1
        self._now = -math.inf
        self._in_gap = False

    @property
    def pending(self) -> int:
        return len(self._queue)

    def _clock(self, now_ms: float) -> None:
        if not math.isfinite(now_ms) or now_ms < self._now:
            raise AudioFormatError("Use one finite monotonic arrival/playout clock per stream")
        self._now = now_ms

    def push(self, packet: AudioPacket, *, now_ms: float) -> bool:
        self._clock(now_ms)
        self.metrics.received += 1
        if (
            type(packet.sequence) is not int
            or packet.sequence < 0
            or type(packet.timestamp_ms) is not int
            or packet.timestamp_ms < 0
            or not isinstance(packet.payload, bytes)
            or len(packet.payload) != 160
        ):
            self.metrics.invalid += 1
            return False
        if packet.sequence < self.expected:
            self.metrics.late += 1
            return False
        if packet.sequence in self._queue:
            self.metrics.duplicates += 1
            return False
        if len(self._queue) >= self.max_packets:
            self.metrics.overflow += 1
            return False
        if self._anchor is None and packet.sequence >= self.expected + self.max_packets:
            self.metrics.overflow += 1
            return False
        if self._anchor is None:
            origin_timestamp = packet.timestamp_ms - (packet.sequence - self.expected) * 20
            if origin_timestamp < 0:
                self.metrics.invalid += 1
                return False
            self._anchor = self.expected, origin_timestamp, now_ms
        sequence, timestamp, _ = self._anchor
        if packet.timestamp_ms != timestamp + (packet.sequence - sequence) * 20:
            self.metrics.invalid += 1
            return False
        if packet.sequence < self._highest:
            self.metrics.reordered += 1
        self._highest = max(self._highest, packet.sequence)
        self._queue[packet.sequence] = packet, now_ms
        self.metrics.max_depth = max(self.metrics.max_depth, len(self._queue))
        return True

    def drain(self, *, now_ms: float) -> tuple[Playout, ...]:
        self._clock(now_ms)
        if self._anchor is None:
            return ()
        sequence, timestamp, arrival = self._anchor
        out = []
        while self._queue:
            delta = (self.expected - sequence) * 20
            deadline = arrival + self.delay_ms + delta
            if deadline > now_ms:
                break
            entry = self._queue.pop(self.expected, None)
            if entry is None:
                next_sequence = min(self._queue)
                due_sequence = sequence + math.floor((now_ms - arrival - self.delay_ms) / 20)
                count = min(next_sequence, due_sequence + 1) - self.expected
                self.metrics.sequence_gaps += count
                if not self._in_gap:
                    self.metrics.gap_events += 1
                self._in_gap = True
                out.append(Playout(self.expected, timestamp + delta, None, count))
                self.expected += count
                continue
            else:
                packet, received = entry
                self.metrics.max_wait_ms = max(self.metrics.max_wait_ms, now_ms - received)
                self.metrics.emitted += 1
                self._in_gap = False
                out.append(Playout(packet.sequence, packet.timestamp_ms, packet.payload))
            self.expected += 1
        return tuple(out)

    def discard(self) -> int:
        count = len(self._queue)
        self.metrics.discarded += count
        self._queue.clear()
        return count
