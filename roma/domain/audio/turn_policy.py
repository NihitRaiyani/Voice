"""One configuration boundary for the isolated half-duplex prototype turn."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnPolicy:
    endpoint_silence_ms: int = 512
    continuation_grace_ms: int = 300
    batch_gap_min_ms: int = 256
    max_utterance_ms: int = 30000
    batch_min_turn_ms: int = 2000

    def __post_init__(self):
        values = (self.endpoint_silence_ms, self.continuation_grace_ms,
                  self.batch_gap_min_ms, self.max_utterance_ms, self.batch_min_turn_ms)
        if any(type(v) is not int for v in values):
            raise ValueError("Turn policy durations must be integer milliseconds")
        if not 0 < self.batch_gap_min_ms < self.endpoint_silence_ms:
            raise ValueError("The batch gap must be below the endpoint silence window")
        if not (0 <= self.continuation_grace_ms <= 2000 and
                0 < self.endpoint_silence_ms <= 2000 and
                self.endpoint_silence_ms < self.max_utterance_ms <= 30000 and
                0 <= self.batch_min_turn_ms <= 30000):
            raise ValueError("Turn policy exceeds bounded prototype durations")

    @property
    def batch_detector_silence_ms(self):
        return min(384, self.endpoint_silence_ms)

    @property
    def endpoint_wait_ms(self):
        return self.endpoint_silence_ms + self.continuation_grace_ms
