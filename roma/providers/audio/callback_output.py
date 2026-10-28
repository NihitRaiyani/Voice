"""Bounded PCM handoff for a persistent 16 kHz native output callback.

The callback copies bytes and metadata only. It never calls a PortAudio lifecycle
API, takes a lock, performs DSP, or waits for the event loop.
"""

import math
from collections import deque


class CallbackOutput:
    frame_bytes = 640
    capacity = 4  # At most 80 ms queued; the device buffer is measured separately.

    def __init__(self, *, generation, current_epoch, clock):
        self.generation = generation
        self.current_epoch = current_epoch
        self.clock = clock
        self.frames = deque()
        self.receipts = deque()
        self.first_write_at = None
        self.last_end_at = None
        self.callback_count = 0
        self.acknowledged_generation = -1
        self.error = None
        self.underflows = 0
        self.receipt_overflows = 0
        self.stalls = 0
        self.high_watermark = 0
        self.stale_discarded = 0
        self.starvations = 0
        self.producer = None
        self.producer_complete = False
        self.last_enqueued = 0
        self.last_handed = 0
        self._reference_anchor = None
        self._reference_anchor_callback = 0
        self.reference_clock_uncertainty_frames = 0
        self.reference_clock_uncertainty_max_ms = 0.0

    @property
    def pending_handoff(self):
        return self.producer is not None and self.last_handed < self.last_enqueued

    def begin_clip(self, *, generation, epoch):
        self.producer = (generation, epoch)
        self.producer_complete = False

    def finish_clip(self):
        self.producer_complete = True

    def enqueue(self, pcm, *, generation, epoch):
        if len(pcm) != self.frame_bytes:
            raise ValueError("Output requires complete 20 ms PCM16 blocks")
        if generation != self.generation() or not self.current_epoch(epoch):
            return False
        if len(self.frames) >= self.capacity:
            return False
        self.last_enqueued += 1
        self.frames.append((self.last_enqueued, generation, epoch, pcm))
        self.high_watermark = max(self.high_watermark, len(self.frames))
        return True

    def clear(self):
        self.producer = None
        self.frames.clear()

    def pop_receipt(self):
        return self.receipts.popleft() if self.receipts else None

    def callback(self, outdata, frames, time_info, status):
        outdata[:] = bytes(len(outdata))
        self.callback_count += 1
        generation = self.generation()
        if getattr(status, "output_underflow", False):
            self.underflows += 1
            self.error = "Speaker underflow"
        if frames != 320 or len(outdata) != self.frame_bytes:
            self.error = "Invalid native output block"
        if self.error is not None:
            self.acknowledged_generation = generation
            return
        if not self.frames:
            producer = self.producer
            if (producer is not None and producer[0] == generation
                    and self.current_epoch(producer[1])
                    and self.first_write_at is not None and not self.producer_complete):
                self.starvations += 1
                self.error = "Output PCM queue starved"
            self.acknowledged_generation = generation
            return
        try:
            handoff_id, queued_generation, epoch, pcm = self.frames.popleft()
        except IndexError:
            # Async clear may empty the deque after the preceding empty check.
            self.acknowledged_generation = self.generation()
            return
        if queued_generation != generation or not self.current_epoch(epoch):
            self.stale_discarded += 1
            self.acknowledged_generation = generation
            return
        current = getattr(time_info, "currentTime", None)
        dac = getattr(time_info, "outputBufferDacTime", None)
        if (type(current) not in (int, float) or type(dac) not in (int, float)
                or not math.isfinite(current) or not math.isfinite(dac)
                or not 0 <= dac - current <= 1):
            self.error = "Invalid native output clock"
            self.acknowledged_generation = generation
            return
        if len(self.receipts) >= 32:
            self.receipt_overflows += 1
            self.error = "Output reference receipt overflow"
            self.acknowledged_generation = generation
            return
        now = self.clock()
        observed_at = now + dac - current
        # Clock access may yield to an invalidating thread. Fence again at the
        # native handoff; clear waits a subsequent callback acknowledgement too.
        if queued_generation != self.generation() or not self.current_epoch(epoch):
            self.stale_discarded += 1
            self.acknowledged_generation = self.generation()
            return
        if self._reference_anchor is None:
            self._reference_anchor = observed_at
            self._reference_anchor_callback = self.callback_count
        # One anchored sample clock owns render alignment. Every native callback
        # counts, including silence; per-callback scheduling/clock estimates may
        # vary without implying that delivered PCM overlapped or disappeared.
        at = self._reference_anchor + (self.callback_count - self._reference_anchor_callback) * .020
        uncertainty_ms = abs(observed_at - at) * 1000
        self.reference_clock_uncertainty_frames += int(uncertainty_ms > 5)
        self.reference_clock_uncertainty_max_ms = max(
            self.reference_clock_uncertainty_max_ms, uncertainty_ms)
        outdata[:] = pcm
        self.receipts.append((pcm, at))
        self.first_write_at = self.first_write_at if self.first_write_at is not None else now
        self.last_end_at = at + 0.020
        # Queue removal is not completion: publish this only after the native
        # buffer, render receipt and tail timestamp all belong to this handoff.
        self.last_handed = handoff_id
        self.acknowledged_generation = generation

    def health(self):
        return {
            "output_callback_count": self.callback_count,
            "output_queue_capacity_frames": self.capacity,
            "output_queue_occupancy_frames": len(self.frames),
            "output_queue_high_watermark_frames": self.high_watermark,
            "output_reference_overflows": self.receipt_overflows,
            "output_callback_stalls": self.stalls,
            "output_stale_frames_discarded": self.stale_discarded,
            "output_queue_starvations": self.starvations,
            "output_reference_clock_uncertainty_frames": self.reference_clock_uncertainty_frames,
            "output_reference_clock_uncertainty_max_ms": self.reference_clock_uncertainty_max_ms,
        }
