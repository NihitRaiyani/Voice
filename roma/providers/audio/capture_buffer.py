"""Bounded callback-to-async PCM handoff; callbacks never wait for the consumer."""

import threading
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter


class CaptureMode(StrEnum):
    ACTIVE = "ACTIVE"
    MUTED_HALF_DUPLEX = "MUTED_HALF_DUPLEX"
    CLOSED = "CLOSED"


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
        self.last_callback_at = clock()
        self._failed_generation = None
        self.muted_discarded = 0
        self.stale_discarded = 0
        self.active_overflows = 0

    def activate(self, epoch):
        with self._lock:
            if self.mode == CaptureMode.CLOSED:
                raise RuntimeError("Capture is closed")
            self._frames.clear()
            self.generation += 1
            self.epoch = epoch
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

    def push(self, pcm, frames, epoch, overflow=False):
        self.last_callback_at = self.clock()
        if self.mode != CaptureMode.ACTIVE:
            self.muted_discarded += 1
            return
        if not self._lock.acquire(blocking=False):
            # A fixed flag is sufficient: an active turn with any lost block fails.
            self._failed_generation = self.generation
            self.active_overflows += 1
            return
        try:
            if self.mode != CaptureMode.ACTIVE:
                self.muted_discarded += 1
                return
            if epoch != self.epoch:
                self.stale_discarded += 1
                return
            if (
                overflow
                or type(frames) is not int
                or frames != 320
                or len(pcm) != frames * 2
                or len(self._frames) >= self.capacity
            ):
                self._failed_generation = self.generation
                self.active_overflows += 1
                self._frames.clear()
                return
            self._frames.append(
                CaptureFrame(bytes(pcm), self.last_callback_at, epoch, self.generation)
            )
        finally:
            self._lock.release()

    def check(self, generation):
        if self._failed_generation == generation:
            raise ValueError("Microphone overflow: incomplete utterance discarded")

    def pop(self, generation, epoch):
        with self._lock:
            self.check(generation)
            while self._frames:
                frame = self._frames.popleft()
                if frame.generation == generation and frame.epoch == epoch:
                    return frame
                self.stale_discarded += 1
            return None
