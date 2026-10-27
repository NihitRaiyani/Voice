"""Bounded queues with hysteresis, explicit loss policy and constant-size metrics."""

import asyncio
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from time import perf_counter

from roma.core.telemetry import get_telemetry


class QueuePolicy(StrEnum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"


class QueuePressure(RuntimeError):
    def __init__(self):
        super().__init__("Queue pressure")


@dataclass
class QueueMetrics:
    capacity: int
    peak_depth: int = 0
    dropped_oldest: int = 0
    dropped_newest: int = 0
    discarded: int = 0
    high_water_hits: int = 0
    wait_ms: float = 0.0
    put_wait_ms: float = 0.0
    get_wait_ms: float = 0.0


class BoundedChannel:
    def __init__(self, name, *, capacity=8, high=6, low=2,
                 policy=QueuePolicy.DROP_NEWEST, wait_timeout=0):
        if any(type(v) is not int for v in (capacity, high, low)) or not 0 <= low < high <= capacity <= 256:
            raise ValueError("Invalid queue watermarks")
        if name not in {"audio_in", "partials", "finals", "tokens", "chunks", "safe_chunks", "tts_text", "tts_audio", "audio"}:
            raise ValueError("Use a reviewed queue name")
        if not isinstance(policy, QueuePolicy) or not math.isfinite(wait_timeout) or not 0 <= wait_timeout <= 30:
            raise ValueError("Invalid queue policy")
        self.name, self.high, self.low = name, high, low
        self.policy, self.wait_timeout = policy, wait_timeout
        self.metrics = QueueMetrics(capacity)
        self._queue = asyncio.Queue(maxsize=capacity)
        self._changed = asyncio.Condition()
        self._closed = False
        self.pressured = False
        self._reported_depth = 0

    @property
    def depth(self):
        return self._queue.qsize()

    def snapshot(self):
        return {**asdict(self.metrics), "depth": self.depth}

    def _publish(self, *, wait_seconds=None, dropped=0):
        telemetry = get_telemetry()
        telemetry.adjust_queue_depth(self.name, self.depth - self._reported_depth)
        self._reported_depth = self.depth
        if wait_seconds is not None:
            telemetry.observe_latency('queue_wait', wait_seconds)
        if dropped:
            telemetry.queue_drops(self.name, dropped)

    async def put(self, item):
        started = perf_counter()
        async with self._changed:
            dropped_before = self.metrics.dropped_oldest + self.metrics.dropped_newest
            try:
                if self._closed:
                    return False
                if self.pressured and self.wait_timeout:
                    try:
                        async with asyncio.timeout(self.wait_timeout):
                            await self._changed.wait_for(lambda: not self.pressured or self._closed)
                    except TimeoutError:
                        self.metrics.dropped_newest += 1
                        raise QueuePressure() from None
                if self._closed:
                    return False
                if self.pressured:
                    if self.policy == QueuePolicy.DROP_NEWEST:
                        self.metrics.dropped_newest += 1
                        return False
                    self._queue.get_nowait()
                    self._queue.task_done()
                    self.metrics.dropped_oldest += 1
                self._queue.put_nowait(item)
                self.metrics.peak_depth = max(self.metrics.peak_depth, self.depth)
                if self.depth >= self.high and not self.pressured:
                    self.pressured = True
                    self.metrics.high_water_hits += 1
                self._changed.notify_all()
                return True
            finally:
                elapsed = (perf_counter() - started) * 1000
                self.metrics.wait_ms += elapsed
                self.metrics.put_wait_ms += elapsed
                dropped = self.metrics.dropped_oldest + self.metrics.dropped_newest - dropped_before
                self._publish(wait_seconds=elapsed / 1000, dropped=dropped)

    async def get(self):
        started = perf_counter()
        async with self._changed:
            try:
                await self._changed.wait_for(lambda: self.depth or self._closed)
                if not self.depth:
                    raise StopAsyncIteration
                item = self._queue.get_nowait()
                self._queue.task_done()
                if self.depth <= self.low:
                    self.pressured = False
                self._changed.notify_all()
                return item
            finally:
                elapsed = (perf_counter() - started) * 1000
                self.metrics.wait_ms += elapsed
                self.metrics.get_wait_ms += elapsed
                self._publish(wait_seconds=elapsed / 1000)

    async def close(self, *, discard=False):
        async with self._changed:
            self._closed = True
            if discard:
                while self.depth:
                    self._queue.get_nowait()
                    self._queue.task_done()
                    self.metrics.discarded += 1
            self._changed.notify_all()
            self._publish()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.get()
