"""Bounded token -> punctuation -> audited text pipeline; no raw token reaches TTS."""

import asyncio
import math
from dataclasses import dataclass

from roma.core.async_queue import BoundedChannel, QueuePolicy, QueuePressure


@dataclass(frozen=True)
class StreamConfig:
    capacity: int = 8
    high: int = 6
    low: int = 2
    queue_timeout_secs: float = 2.0
    playback_timeout_secs: float = 30.0
    cleanup_timeout_secs: float = 1.0
    max_chunk_chars: int = 512
    max_turn_chars: int = 4096
    minimum_speech_ms: int = 600
    speech_threshold: float = 0.8

    def __post_init__(self):
        if any(type(v) is not int for v in (self.capacity, self.high, self.low, self.max_chunk_chars, self.max_turn_chars, self.minimum_speech_ms)):
            raise ValueError("Streaming limits must be integral")
        if not 0 <= self.low < self.high <= self.capacity <= 32:
            raise ValueError("Invalid streaming watermarks")
        if not 8 <= self.max_chunk_chars <= 1024 or not self.max_chunk_chars <= self.max_turn_chars <= 4096:
            raise ValueError("Invalid streaming text limits")
        for duration in (self.queue_timeout_secs, self.playback_timeout_secs, self.cleanup_timeout_secs):
            if not math.isfinite(duration) or not 0 < duration <= 30:
                raise ValueError("Invalid streaming timeout")
        if not 100 <= self.minimum_speech_ms <= 2000 or not math.isfinite(self.speech_threshold) or not 0 < self.speech_threshold <= 1:
            raise ValueError("Invalid barge-in threshold")

    def channel(self, name):
        # Ordered generated/validated speech: backpressure, then counted DropNewest
        # and fail the turn. Never silently skip words and continue the reply.
        return BoundedChannel(name, capacity=self.capacity, high=self.high, low=self.low,
                              policy=QueuePolicy.DROP_NEWEST, wait_timeout=self.queue_timeout_secs)


class PunctuationChunker:
    def __init__(self, *, max_chars=512):
        self.max_chars = max_chars
        self._tail = ""

    def feed(self, text):
        if not isinstance(text, str):
            raise ValueError("Invalid token text")
        self._tail += text
        output, start = [], 0
        for index, char in enumerate(self._tail):
            if char not in ",.!?।":
                continue
            if char in ",." and index and self._tail[index - 1].isdecimal():
                if index == len(self._tail) - 1 or self._tail[index + 1].isdecimal():
                    continue
            line = self._tail[start:index + 1].strip()
            if len(line) > self.max_chars:
                raise ValueError("Streaming clause exceeds limit")
            if line:
                output.append(line)
            start = index + 1
        self._tail = self._tail[start:]
        if len(self._tail) > self.max_chars:
            raise ValueError("Streaming clause exceeds limit")
        return tuple(output)

    def finish(self):
        tail, self._tail = self._tail.strip(), ""
        return (tail,) if tail else ()


class StreamingText:
    def __init__(self, policy, *, metrics):
        self.policy, self.metrics = policy, metrics
        self.channels = {name: policy.channel(name) for name in ("tokens", "chunks", "safe_chunks")}
        self.parts = []
        self._stopped = False
        self._chars = 0

    async def run(self, produce, guard, emit):
        tokens, chunks, safe = (self.channels[name] for name in ("tokens", "chunks", "safe_chunks"))
        async def feed(text):
            self._chars += len(text)
            if self._chars > self.policy.max_turn_chars:
                raise ValueError("Streaming turn exceeds limit")
            if text and not await tokens.put(text):
                raise QueuePressure()
        async def source():
            try:
                await produce(feed)
            finally:
                await tokens.close()
        async def chunk():
            chunker = PunctuationChunker(max_chars=self.policy.max_chunk_chars)
            try:
                async for token in tokens:
                    if self._stopped:
                        continue
                    for line in chunker.feed(token):
                        if not await chunks.put(line):
                            raise QueuePressure()
                if not self._stopped:
                    for line in chunker.finish():
                        if not await chunks.put(line):
                            raise QueuePressure()
            finally:
                await chunks.close()
        async def check():
            prefix = ""
            try:
                async for line in chunks:
                    if self._stopped:
                        continue
                    prefix = (prefix + " " + line).strip()
                    checked, stop = await guard(prefix, line)
                    if not await safe.put(checked):
                        raise QueuePressure()
                    if stop:
                        self._stopped = True
                        producer.cancel()
            finally:
                await safe.close()
        async def deliver():
            async for line in safe:
                await emit(line)
                self.parts.append(line)
        try:
            async with asyncio.TaskGroup() as group:
                producer = group.create_task(source())
                group.create_task(chunk())
                group.create_task(check())
                group.create_task(deliver())
        finally:
            for name, channel in self.channels.items():
                await channel.close(discard=True)
                self.metrics[name] = channel.snapshot()
        return " ".join(self.parts)
