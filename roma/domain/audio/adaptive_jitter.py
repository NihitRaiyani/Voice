"""Opt-in normalized PCM16 laboratory; fixed/carrier playout stays unchanged.

RFC3550 A.8 transit variation is an EWMA of absolute differences, not squared
jitter variance. RTP parsing, sequence wraparound and native quality are outside
this normalized, epoch-scoped 20 ms seam.
"""

import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from numbers import Real

from roma.domain.audio.pcm import AudioFormatError, pack, unpack

FRAME_MS = 20
FRAME_BYTES = 640
MAX_INTEGER = 2**53 - 1


def integer(value, *, minimum=0, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AudioFormatError("Expected a bounded integer")
    return value


@dataclass(frozen=True)
class PacketEnvelope:
    seq_no: int
    timestamp_ms: int
    turn_epoch: int
    payload_hash: str

    def __post_init__(self):
        integer(self.seq_no)
        integer(self.timestamp_ms)
        integer(self.turn_epoch)
        if not isinstance(self.payload_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.payload_hash):
            raise AudioFormatError("Expected a SHA256 payload identity")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"seq_no", "timestamp_ms", "turn_epoch", "payload_hash"}:
            raise AudioFormatError("Invalid packet envelope fields")
        return cls(**value)


@dataclass(frozen=True)
class PCMPlayout:
    seq_no: int
    timestamp_ms: int
    turn_epoch: int
    pcm: bytes | None
    concealed: bool = False
    missing_frames: int = 0


@dataclass
class AdaptiveMetrics:
    received: int = 0
    emitted: int = 0
    duplicates: int = 0
    late: int = 0
    invalid: int = 0
    overflow: int = 0
    reordered: int = 0
    stale_epoch: int = 0
    discarded: int = 0
    max_depth: int = 0
    gap_frames: int = 0
    gap_events: int = 0
    concealed_frames: int = 0
    interpolated_frames: int = 0
    discontinuities: int = 0
    max_wait_ms: float = 0
    max_playout_lateness_ms: float = 0
    max_target_delay_ms: int = 40

    def snapshot(self):
        return asdict(self)


class AdaptiveJitterBuffer:
    """One 16 kHz mono PCM16 stream with a finite monotonic injected clock.

    The 40..120 ms target offsets the anchored media timeline. Receiver wait can
    differ with early arrivals; timer lateness and observed wait are measured
    separately rather than presenting that target as a native latency guarantee.
    """

    def __init__(self, *, clock: Callable[[], float], turn_epoch=0, first_sequence=1,
                 max_packets=16, max_sequence_ahead=300):
        integer(turn_epoch)
        integer(first_sequence)
        integer(max_packets, minimum=3, maximum=128)
        integer(max_sequence_ahead, minimum=max_packets, maximum=15000)
        if not callable(clock):
            raise AudioFormatError("An injected clock is required")
        self.clock, self.turn_epoch, self.expected = clock, turn_epoch, first_sequence
        self.max_packets, self.max_sequence_ahead = max_packets, max_sequence_ahead
        self.metrics = AdaptiveMetrics()
        self._queue: dict[int, tuple[PacketEnvelope, bytes, float]] = {}
        self._anchor = None
        self.jitter_ms = 0.0
        self.target_delay_ms = 40
        self._transit = None
        self._last_deadline = -math.inf
        self._now = -math.inf
        self._highest = first_sequence - 1
        self._last_pcm = None
        self._last_observed_sequence = None
        self._loss_run = 0
        self._closed = False

    @property
    def pending(self):
        return len(self._queue)

    def _clock(self):
        now = self.clock()
        if isinstance(now, bool) or not isinstance(now, Real) or not math.isfinite(now) or now < max(0, self._now):
            raise AudioFormatError("Use one finite monotonic millisecond clock")
        self._now = now
        return now

    def push(self, envelope, pcm):
        now = self._clock()
        self.metrics.received += 1
        if (not isinstance(envelope, PacketEnvelope) or not isinstance(pcm, bytes)
                or len(pcm) != FRAME_BYTES or sha256(pcm).hexdigest() != envelope.payload_hash):
            self.metrics.invalid += 1
            return False
        if envelope.turn_epoch != self.turn_epoch:
            self.metrics.stale_epoch += 1
            return False
        if self._closed:
            self.metrics.invalid += 1
            return False
        if envelope.seq_no < self.expected:
            self.metrics.late += 1
            return False
        if envelope.seq_no in self._queue:
            self.metrics.duplicates += 1
            return False
        if len(self._queue) >= self.max_packets or envelope.seq_no - self.expected > self.max_sequence_ahead:
            self.metrics.overflow += 1
            return False
        origin = envelope.timestamp_ms - (envelope.seq_no - self.expected) * FRAME_MS
        if self._anchor is None:
            if origin < 0:
                self.metrics.invalid += 1
                return False
            self._anchor = self.expected, origin, now
        sequence, timestamp, _ = self._anchor
        if envelope.timestamp_ms != timestamp + (envelope.seq_no - sequence) * FRAME_MS:
            self.metrics.invalid += 1
            return False
        transit = now - envelope.timestamp_ms
        if self._transit is not None:
            self.jitter_ms += (abs(transit - self._transit) - self.jitter_ms) / 16
        self._transit = transit
        requested = min(120, max(40, math.ceil(40 + 4 * self.jitter_ms)))
        self.target_delay_ms = max(self.target_delay_ms - 20, min(self.target_delay_ms + 20, requested))
        self.metrics.max_target_delay_ms = max(self.metrics.max_target_delay_ms, self.target_delay_ms)
        if envelope.seq_no < self._highest:
            self.metrics.reordered += 1
        self._highest = max(self._highest, envelope.seq_no)
        self._queue[envelope.seq_no] = envelope, pcm, now
        self.metrics.max_depth = max(self.metrics.max_depth, self.pending)
        return True

    def _conceal(self):
        left = unpack(self._last_pcm)
        future = min(self._queue) if self._queue else None
        if future is not None:
            right = unpack(self._queue[future][1])
            distance = future - self._last_observed_sequence
            step = self.expected - self._last_observed_sequence
            samples = [(a * (distance - step) + b * step) for a, b in zip(left, right, strict=True)]
            self.metrics.interpolated_frames += 1
        else:
            samples, distance = left, 1
        numerator, denominator = 3 ** self._loss_run, distance * 4 ** self._loss_run
        attenuated = [(abs(sample) * numerator // denominator) * (-1 if sample < 0 else 1)
                      for sample in samples]
        return pack(attenuated)

    def drain(self):
        now = self._clock()
        return self._release(now)

    def flush(self, *, last_seq_no=None):
        """Explicit end of input, including a declared trailing loss; never waits for another arrival."""
        now = self._clock()
        if last_seq_no is not None:
            integer(last_seq_no)
            if not self.expected - 1 <= last_seq_no <= self.expected + self.max_sequence_ahead:
                raise AudioFormatError("Tail exceeds the bounded sequence window")
            if self._queue and last_seq_no < max(self._queue):
                raise AudioFormatError("Tail cannot exclude queued packets")
        if self._closed:
            if last_seq_no is not None and last_seq_no >= self.expected:
                raise AudioFormatError("A closed epoch cannot extend its final sequence")
            return ()
        last = last_seq_no if last_seq_no is not None else max(self._queue, default=self.expected - 1)
        output = self._release(now, final_sequence=last)
        self._closed = True
        return output

    def advance_epoch(self, turn_epoch, *, first_sequence=1):
        integer(turn_epoch)
        integer(first_sequence)
        if turn_epoch <= self.turn_epoch:
            raise AudioFormatError("Turn epoch must increase")
        self._clock()
        self.metrics.discarded += self.pending
        self._queue.clear()
        self.turn_epoch, self.expected = turn_epoch, first_sequence
        self._anchor, self._transit = None, None
        self._highest = first_sequence - 1
        self.jitter_ms, self.target_delay_ms = 0.0, 40
        self._last_deadline = -math.inf
        self._last_pcm, self._last_observed_sequence = None, None
        self._loss_run, self._closed = 0, False

    def _release(self, now, *, final_sequence=None):
        if self._anchor is None:
            return ()
        sequence, timestamp, arrival = self._anchor
        output = []
        while self._queue or (final_sequence is not None and self.expected <= final_sequence):
            delta = (self.expected - sequence) * FRAME_MS
            deadline = max(self._last_deadline, arrival + delta + self.target_delay_ms)
            if final_sequence is None and deadline > now:
                break
            entry = self._queue.pop(self.expected, None)
            if entry is not None:
                envelope, pcm, received = entry
                output.append(PCMPlayout(envelope.seq_no, envelope.timestamp_ms, self.turn_epoch, pcm))
                self._last_pcm, self._last_observed_sequence = pcm, self.expected
                self._loss_run = 0
                self.metrics.emitted += 1
                self.metrics.max_wait_ms = max(self.metrics.max_wait_ms, now - received)
            else:
                if self._loss_run == 0:
                    self.metrics.gap_events += 1
                self._loss_run += 1
                if self._last_pcm is not None and self._loss_run <= 3:
                    output.append(PCMPlayout(self.expected, timestamp + delta, self.turn_epoch,
                                             self._conceal(), concealed=True, missing_frames=1))
                    self.metrics.concealed_frames += 1
                    count = 1
                else:
                    due = (final_sequence if final_sequence is not None else
                           sequence + math.floor((now - arrival - self.target_delay_ms) / FRAME_MS))
                    next_sequence = min(self._queue) if self._queue else final_sequence + 1
                    count = min(next_sequence, due + 1) - self.expected
                    output.append(PCMPlayout(self.expected, timestamp + delta, self.turn_epoch,
                                             None, missing_frames=count))
                    self.metrics.discontinuities += 1
                self.metrics.gap_frames += count
                self._loss_run += count - 1
                self.expected += count - 1
            self.metrics.max_playout_lateness_ms = max(self.metrics.max_playout_lateness_ms, now - deadline)
            self._last_deadline = deadline
            self.expected += 1
        return tuple(output)
