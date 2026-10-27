"""Call-owned PCM admission, released only by completed software writes.

Reserve at the producer before pushing a frame into Pipecat's unbounded queues.
The transport must capture ``epoch`` before its awaited write, then acknowledge
that epoch afterward. Reset wakes old producers and ignores stale write credits.
"""

import asyncio
import math
from dataclasses import replace

from pipecat.frames.frames import OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketOutputTransport,
    FastAPIWebsocketTransport,
)


class PlaybackPressure(RuntimeError):
    def __init__(self):
        super().__init__('Playback capacity exceeded')


class PlaybackWindow:
    def __init__(self, *, max_audio_secs=.4, enqueue_timeout_secs=.25, on_pressure=None):
        if (type(max_audio_secs) not in (int, float) or not math.isfinite(max_audio_secs)
                or not .02 <= max_audio_secs <= 5):
            raise ValueError('Invalid playback audio bound')
        if (type(enqueue_timeout_secs) not in (int, float)
                or not math.isfinite(enqueue_timeout_secs) or not 0 < enqueue_timeout_secs <= 10):
            raise ValueError('Invalid playback wait bound')
        self.max_bytes = int(max_audio_secs * 8000) * 2
        self.enqueue_timeout_secs = enqueue_timeout_secs
        self.on_pressure = on_pressure
        self.queued_bytes = 0
        self.epoch = 0
        self.failed = False
        self._changed = asyncio.Event()

    @property
    def queued_audio_secs(self):
        return self.queued_bytes / 16000

    async def reserve(self, size, *, epoch=None):
        if type(size) is not int or not 0 < size <= self.max_bytes or size % 2:
            raise ValueError('Invalid playback PCM size')
        epoch = self.epoch if epoch is None else epoch
        if epoch != self.epoch:
            raise asyncio.CancelledError('Stale playback')
        if self.failed:
            raise PlaybackPressure()
        try:
            async with asyncio.timeout(self.enqueue_timeout_secs):
                while self.queued_bytes + size > self.max_bytes:
                    await self._changed.wait()
                    self._changed.clear()
                    if epoch != self.epoch:
                        raise asyncio.CancelledError('Stale playback')
                    if self.failed:
                        raise PlaybackPressure()
        except TimeoutError:
            notify = not self.failed
            self.failed = True
            self._changed.set()
            if notify and self.on_pressure is not None:
                try:
                    async with asyncio.timeout(.5):
                        await self.on_pressure()
                except Exception:  # noqa: BLE001 — closure failure must retain the latched pressure error.
                    pass
            raise PlaybackPressure() from None
        self.queued_bytes += size
        return epoch

    def reset(self):
        """Invalidate old producers and acknowledgments after queued audio is cleared."""
        self.epoch += 1
        self.queued_bytes = 0
        self._changed.set()
        return self.epoch

    def acknowledge(self, size, *, epoch):
        if epoch == self.epoch:
            self.queued_bytes = max(0, self.queued_bytes - size)
            self._changed.set()


class PlaybackAdmissionProcessor(FrameProcessor):
    """Bound transport admission for fixed raw assets; native TTS reserves upstream.

    The controller owns reset on interruption before awaiting processor cancellation.
    Direct mode avoids adding another unbounded processor queue at this boundary.
    """

    def __init__(self, window):
        super().__init__(enable_direct_mode=True)
        self.window = window

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, OutputAudioRawFrame):
            if frame.sample_rate != 8000 or frame.num_channels != 1 or len(frame.audio) % 2:
                raise ValueError('Playback requires mono 8 kHz PCM16')
            epoch = self.window.epoch
            reserved = getattr(frame, '_roma_playback_generation', None)
            if reserved is not None:
                if type(reserved) is int and reserved == epoch:
                    await self.push_frame(frame, direction)
                return
            for offset in range(0, len(frame.audio), 320):
                # Match MediaSender's full-frame writes, including the final silence
                # padding, so a partial tail cannot refund the next sentence's credit.
                pcm = frame.audio[offset:offset+320].ljust(320, b'\0')
                chunk = replace(frame, audio=pcm)
                await self.window.reserve(len(chunk.audio), epoch=epoch)
                chunk._roma_playback_generation = epoch
                await self.push_frame(chunk, direction)
            return
        await self.push_frame(frame, direction)


class WindowedOutputTransport(FastAPIWebsocketOutputTransport):
    def __init__(self, *args, window, **kwargs):
        super().__init__(*args, **kwargs)
        self.window = window

    async def write_audio_frame(self, frame):
        # MediaSender clones/re-chunks input frames, losing arbitrary attributes.
        # Snapshot before await so an interrupted old write cannot refund new PCM.
        epoch = self.window.epoch
        written = await super().write_audio_frame(frame)
        if written:
            self.window.acknowledge(len(frame.audio), epoch=epoch)
        return written


class WindowedWebsocketTransport(FastAPIWebsocketTransport):
    """Pipecat 1.6 factory seam with write-completion PCM acknowledgments."""

    def __init__(self, *args, window, **kwargs):
        super().__init__(*args, **kwargs)
        self._output = WindowedOutputTransport(
            self, self._client, self._params, window=window, name=self._output_name)
