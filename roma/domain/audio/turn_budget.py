"""Optional reporting thresholds; defaults impose no acceptance criteria."""
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

BUDGET_STAGES = frozenset({"endpoint_wait_ms", "asr_ms", "llm_ms", "safety_ms", "tts_ms", "playback_start_ms", "speech_to_first_audio_ms"})

@dataclass(frozen=True)
class TurnBudget:
    thresholds: Mapping[str, float] | None = field(default=None)

    def __post_init__(self):
        values = dict(self.thresholds or {})
        if not values.keys() <= BUDGET_STAGES or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values.values()):
            raise ValueError("Invalid turn budget configuration")
        object.__setattr__(self, "thresholds", values)

    def evaluate(self, timings):
        return {"configured": bool(self.thresholds), "over_budget": sorted(
            key for key, value in self.thresholds.items() if key in timings and timings[key] > value
        )}
