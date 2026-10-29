"""Optional local WebRTC AEC and a bounded, audio-clock render reference.

No hardware, model, or DSP module is opened on import. Render timestamps and
capture timestamps describe the FIRST sample, using the same monotonic clock.
Run process() in a serial consumer, outside the native audio callback.
"""

import math
import threading
from time import perf_counter

from roma.domain.audio.pcm import pack, unpack, validate_pcm16
from roma.domain.audio.resampling import PCMResampler


class EchoUnavailable(RuntimeError):
    """The explicitly requested real local AEC cannot run."""


class EchoReferenceOverflow(RuntimeError):
    pass


def _tick(at):
    if type(at) not in {int, float} or not math.isfinite(at) or at < 0:
        raise ValueError("Audio timestamps must be finite monotonic seconds")
    return round(at * 16000)


class RenderReference:
    """Sample-addressed ring: queued future audio becomes reference only at its time."""

    def __init__(self, *, capacity_ms=2000, clock=perf_counter):
        if type(capacity_ms) is not int or not 400 <= capacity_ms <= 5000:
            raise ValueError("Render reference capacity must be 400..5000 ms")
        self.clock = clock
        self.capacity_samples = capacity_ms * 16
        self._samples = {}
        self._lock = threading.Lock()
        self._resampler = PCMResampler(8000, 16000)
        self._last_rate = None
        self._last_end = None
        self.overflows = 0
        self._known_until = None
        self.unknown_frames = 0
        self.unknown_samples = 0
        self.known_silent_samples = 0

    def _prune(self, now):
        oldest = now - self.capacity_samples // 2
        self._samples = {at: sample for at, sample in self._samples.items() if at >= oldest}

    @property
    def pending_samples(self):
        with self._lock:
            return len(self._samples)

    def render(self, pcm, *, rate, at):
        validate_pcm16(pcm)
        if type(rate) is not int or rate not in {8000, 16000}:
            raise ValueError("AEC render accepts mono PCM16 at 8/16 kHz")
        if not 0 < len(pcm) <= rate * 2 // 5:
            raise ValueError("AEC render batch must be nonempty and at most 200 ms")
        start, now = _tick(at), _tick(self.clock())
        length = len(pcm) // 2 * (16000 // rate)
        with self._lock:
            self._prune(now)
            if (
                start < now - self.capacity_samples // 2
                or start + length > now + self.capacity_samples // 2
            ):
                raise ValueError("Render timestamp is outside the bounded reference horizon")
            if len(self._samples) + length > self.capacity_samples:
                self.overflows += 1
                raise EchoReferenceOverflow("Render reference is full")
            if any(start + i in self._samples for i in range(length)):
                raise ValueError("Render reference must not overlap previously scheduled audio")
            if self._last_rate != rate or self._last_end != start:
                self._resampler.reset()
            # The existing FIR preserves interpolation history across contiguous writes.
            # Its 1.94 ms group delay at 8 kHz is part of local alignment calibration.
            reference = self._resampler.feed(pcm) if rate == 8000 else pcm
            self._samples.update(
                (start + i, value) for i, value in enumerate(unpack(reference))
            )
            self._last_rate, self._last_end = rate, start + length

    def advance_through(self, at):
        """Publish callback-confirmed PCM/silence after its receipts are drained."""
        end = _tick(at)
        with self._lock:
            if self._known_until is not None and end < self._known_until:
                raise ValueError("Render coverage must not go backwards")
            self._known_until = end

    def read(self, *, at, require_known=False):
        start = _tick(at)
        with self._lock:
            self._prune(_tick(self.clock()))
            oldest = _tick(self.clock()) - self.capacity_samples // 2
            unknown = sum(
                self._known_until is None or tick >= self._known_until or tick < oldest
                for tick in range(start, start + 320))
            if require_known and unknown:
                self.unknown_frames += 1
                self.unknown_samples += unknown
                raise EchoUnavailable("AEC render reference is not yet available")
            if require_known:
                self.known_silent_samples += sum(start + i not in self._samples for i in range(320))
            return pack(tuple(self._samples.get(start + i, 0) for i in range(320)))

    def clear_future(self, at=None):
        cutoff = _tick(self.clock() if at is None else at)
        with self._lock:
            self._samples = {
                tick: value for tick, value in self._samples.items() if tick < cutoff
            }
            self._last_rate = self._last_end = None
            self._resampler.reset()


class WebRTCEchoCanceller:
    """Pinned real AEC3; absence/errors never return raw capture.

    Noise suppression is opt-in: synthetic double-talk retained substantially
    less near-end energy with NS enabled. The existing downstream VAD is separate.
    """

    def __init__(
        self,
        *,
        stream_delay_ms=40,
        reference_capacity_ms=2000,
        noise_suppression=False,
        clock=perf_counter,
    ):
        if type(stream_delay_ms) is not int or not 0 <= stream_delay_ms <= 500:
            raise ValueError("AEC stream delay must be 0..500 ms")
        self.stream_delay_ms = stream_delay_ms
        if type(noise_suppression) is not bool:
            raise ValueError("AEC noise suppression must be a boolean")
        self.noise_suppression = noise_suppression
        self.reference = RenderReference(capacity_ms=reference_capacity_ms, clock=clock)
        self._process_lock = threading.Lock()
        self._processor = self._np = None
        self._last_end = None
        self.discontinuities = 0
        self._require_reference_coverage = False

    def preflight(self):
        if not self._process_lock.acquire(blocking=False):
            raise EchoUnavailable("AEC requires one serial capture consumer")
        try:
            self._preflight_locked()
        finally:
            self._process_lock.release()

    def _preflight_locked(self):
        if self._processor is not None:
            return
        try:
            import importlib.metadata

            import numpy as np
            from pywebrtc_audio import AudioProcessor

            if importlib.metadata.version("pywebrtc-audio") != "0.2.0":
                raise EchoUnavailable("Local AEC requires pywebrtc-audio==0.2.0")
            self._processor = AudioProcessor(
                sample_rate=16000,
                echo_cancellation=True,
                noise_suppression=self.noise_suppression,
                auto_gain_control=False,
                stream_delay_ms=self.stream_delay_ms,
            )
            self._np = np
        except (ImportError, OSError, RuntimeError, ValueError):
            self._processor = self._np = None
            raise EchoUnavailable(
                "Local full duplex requires the pinned duplex optional dependency"
            ) from None

    def require_reference_coverage(self):
        self._require_reference_coverage = True

    def render(self, pcm, rate, at):
        self.reference.render(pcm, rate=rate, at=at)

    def clear_future(self, at=None):
        # Preserve already rendered reference and the learned echo path: acoustic
        # echo can remain after stopping the output device.
        self.reference.clear_future(at)

    def process(self, near, at):
        validate_pcm16(near)
        if len(near) != 640:
            raise ValueError("AEC capture requires exactly 20 ms of mono 16 kHz PCM16")
        start = _tick(at)
        if not self._process_lock.acquire(blocking=False):
            raise EchoUnavailable("AEC requires one serial capture consumer")
        try:
            self._preflight_locked()
            if self._last_end is not None:
                # Device timestamps occasionally jitter; preserve a continuous
                # sample clock within 5 ms rather than repeatedly relearning AEC.
                if abs(start - self._last_end) <= 80:
                    start = self._last_end
                elif start < self._last_end:
                    raise ValueError("AEC capture timestamps must not overlap or go backwards")
                else:
                    self._processor.reset()
                    self.discontinuities += 1
            self._last_end = start + 320
            np = self._np
            capture = np.frombuffer(near, dtype="<i2").copy()
            far = np.frombuffer(self.reference.read(at=start / 16000, require_known=self._require_reference_coverage), dtype="<i2").copy()
            frames = []
            for offset in (0, 160):
                try:
                    output = self._processor.process(
                        capture[offset : offset + 160], far[offset : offset + 160]
                    )
                except ValueError:
                    raise EchoUnavailable("Local AEC processing failed") from None
                if (
                    not isinstance(output, np.ndarray)
                    or output.dtype != np.dtype("int16")
                    or output.shape != (160,)
                ):
                    raise EchoUnavailable("AEC returned invalid PCM")
                frames.append(output.astype("<i2", copy=False).tobytes())
            return b"".join(frames)
        except EchoUnavailable:
            raise
        except (RuntimeError, OSError):
            raise EchoUnavailable("Local AEC processing failed") from None
        finally:
            self._process_lock.release()
