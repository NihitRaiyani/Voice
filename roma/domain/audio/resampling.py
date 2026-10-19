"""Streaming 2:1 windowed-sinc FIR resampling, implemented directly for the audio lab.

Filtering precedes decimation (no alias-producing sample dropping). Interpolation
zero-stuffs then filters with gain two. History and decimation phase survive chunks.
This reference implementation is not enabled on the production cloud audio path.
"""

import math
from collections import deque

from roma.domain.audio.pcm import AudioFormatError, pack, unpack

TAPS = 63
WORK_RATE = 16000
CUTOFF_HZ = 3600


def _kernel() -> tuple[float, ...]:
    cutoff = CUTOFF_HZ / WORK_RATE
    mid = (TAPS - 1) // 2
    values = []
    for i in range(TAPS):
        n = i - mid
        sinc = 2 * cutoff if n == 0 else math.sin(2 * math.pi * cutoff * n) / (math.pi * n)
        window = 0.54 - 0.46 * math.cos(2 * math.pi * i / (TAPS - 1))
        values.append(sinc * window)
    total = sum(values)
    return tuple(value / total for value in values)


_COEFFICIENTS = _kernel()


class PCMResampler:
    def __init__(self, source_rate: int, target_rate: int) -> None:
        if source_rate not in (8000, 16000) or target_rate not in (8000, 16000):
            raise AudioFormatError("The reference resampler supports 8/16 kHz only")
        self.source_rate, self.target_rate = source_rate, target_rate
        self.reset()

    @property
    def delay_ms(self) -> float:
        return 0.0 if self.source_rate == self.target_rate else (TAPS - 1) / 2 / 16

    def reset(self) -> None:
        self._history: deque[int] = deque([0] * TAPS, maxlen=TAPS)
        self._phase = 0
        self._started = False
        self._finished = False

    def _step(self, sample: int) -> int:
        self._history.appendleft(sample)
        gain = 2 if self.source_rate < self.target_rate else 1
        value = round(
            gain * sum(c * x for c, x in zip(_COEFFICIENTS, self._history, strict=True))
        )
        return max(-32768, min(32767, value))

    def _working(self, samples) -> bytes:
        output = []
        for sample in samples:
            value = self._step(sample)
            if self.source_rate < self.target_rate or self._phase == 0:
                output.append(value)
            self._phase ^= 1
        return pack(output)

    def output_samples(self, input_samples: int) -> int:
        """Predict feed output length without changing FIR history or decimation phase."""
        if type(input_samples) is not int or input_samples < 0:
            raise AudioFormatError("Expected a nonnegative integer sample count")
        if self.source_rate == self.target_rate:
            return input_samples
        if self.source_rate < self.target_rate:
            return input_samples * 2
        return (input_samples + 1 - self._phase) // 2

    def feed(self, pcm: bytes) -> bytes:
        samples = unpack(pcm)
        if self._finished:
            raise AudioFormatError("Resampler finished; reset for a new independent stream")
        if not samples:
            return b""
        self._started = True
        if self.source_rate == self.target_rate:
            return pcm
        expanded = (
            (value for sample in samples for value in (sample, 0))
            if self.source_rate < self.target_rate
            else iter(samples)
        )
        return self._working(expanded)

    def finish(self) -> bytes:
        """Drain the FIR tail once; this adds filter-tail duration, not lost-packet audio."""
        if self._finished:
            return b""
        self._finished = True
        if not self._started or self.source_rate == self.target_rate:
            return b""
        return self._working([0] * (TAPS - 1))
