"""Bounded callback-to-async PCM handoff; callbacks never wait for the consumer."""

import math
import threading
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter


class CaptureMode(StrEnum):
    ACTIVE = "ACTIVE"
    MUTED_HALF_DUPLEX = "MUTED_HALF_DUPLEX"
    CLOSED = "CLOSED"


class CaptureDiscontinuity(ValueError):
    """A capture generation lost input; none of its partial speech may be reused."""


@dataclass(frozen=True, slots=True)
class CaptureFrame:
    pcm: bytes
    at: float
    epoch: int
    generation: int


class CaptureBuffer:
    def __init__(self, capacity=32, *, clock=perf_counter):
        if type(capacity) is not int or not 1 <= capacity <= 256:
            raise ValueError("Capture capacity must be bounded")
        self.capacity, self.clock = capacity, clock
        self._lock = threading.Lock()
        self._frames = deque()
        self.mode = CaptureMode.MUTED_HALF_DUPLEX
        self.generation = 0
        self.epoch = 0
        self.follow_epochs = False
        self.last_callback_at = clock()
        self._failed_generation = None
        self.muted_discarded = 0
        self.stale_discarded = 0
        self.active_overflows = 0
        self.capacity_drops = 0
        self.lock_contentions = 0
        self.invalid_input_blocks = 0
        self.high_watermark = 0

    def health(self):
        """Best-effort cumulative metadata without contending with callbacks."""
        occupancy = len(self._frames)
        high_watermark = self.high_watermark
        return {
            "ring_buffer_capacity_frames": self.capacity,
            "ring_buffer_occupancy_frames": occupancy,
            "ring_buffer_high_watermark_frames": high_watermark,
            "ring_buffer_occupancy_pct": 100 * occupancy / self.capacity,
            "ring_buffer_high_watermark_pct": 100 * high_watermark / self.capacity,
            "ring_buffer_capacity_drops": self.capacity_drops,
            "callback_lock_contentions": self.lock_contentions,
            "invalid_input_blocks": self.invalid_input_blocks,
            "active_capture_losses": self.active_overflows,
            "muted_discarded": self.muted_discarded,
            "stale_discarded": self.stale_discarded,
        }

    def activate(self, epoch, *, follow_epochs=False):
        if type(epoch) is not int or epoch < 0 or type(follow_epochs) is not bool:
            raise ValueError("Invalid capture epoch policy")
        with self._lock:
            if self.mode == CaptureMode.CLOSED:
                raise RuntimeError("Capture is closed")
            self._frames.clear()
            self.generation += 1
            self.epoch = epoch
            self.follow_epochs = follow_epochs
            self._failed_generation = None
            self.mode = CaptureMode.ACTIVE
            return self.generation

    def mute(self):
        with self._lock:
            self._frames.clear()
            if self.mode != CaptureMode.CLOSED:
                self.mode = CaptureMode.MUTED_HALF_DUPLEX

    def close(self):
        with self._lock:
            self._frames.clear()
            self.mode = CaptureMode.CLOSED

    def push(self, pcm, frames, epoch, overflow=False, *, at=None):
        self.last_callback_at = self.clock()
        if self.mode != CaptureMode.ACTIVE:
            self.muted_discarded += 1
            return
        if not self._lock.acquire(blocking=False):
            # A fixed flag is sufficient: an active turn with any lost block fails.
            self._failed_generation = self.generation
            self.active_overflows += 1
            self.lock_contentions += 1
            return
        try:
            if self.mode != CaptureMode.ACTIVE:
                self.muted_discarded += 1
                return
            if not self.follow_epochs and epoch != self.epoch:
                self.stale_discarded += 1
                return
            invalid = (
                (at is not None and (type(at) not in (int, float) or not math.isfinite(at)))
                or type(frames) is not int
                or frames != 320
                or len(pcm) != frames * 2
            )
            capacity_lost = len(self._frames) >= self.capacity
            if overflow or invalid or capacity_lost:
                self.invalid_input_blocks += int(invalid)
                self.capacity_drops += int(capacity_lost)
                self._failed_generation = self.generation
                self.active_overflows += 1
                self._frames.clear()
                return
            self._frames.append(
                CaptureFrame(bytes(pcm), self.last_callback_at if at is None else at, epoch, self.generation)
            )
            self.high_watermark = max(self.high_watermark, len(self._frames))
        finally:
            self._lock.release()

    def check(self, generation):
        if self._failed_generation == generation:
            raise CaptureDiscontinuity("Microphone overflow: incomplete utterance discarded")

    def pop(self, generation, epoch=None):
        with self._lock:
            self.check(generation)
            while self._frames:
                frame = self._frames.popleft()
                if frame.generation == generation and (epoch is None or frame.epoch == epoch):
                    return frame
                self.stale_discarded += 1
            return None
