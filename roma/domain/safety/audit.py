"""PII-free rule activations; domain code has no storage or provider dependency."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from roma.domain.safety import FilterVerdict, screen
from roma.domain.safety.lexicon import HARD_FAIL_LINE

POLICY_VERSION = "roma-safety-2"


@dataclass(frozen=True)
class SafetyContext:
    call_sid: str
    turn_number: int
    stage: str


@dataclass(frozen=True)
class SafetyActivation:
    id: UUID
    rule_id: str
    category: str
    replacement_used: str
    detected_at: datetime
    latency_ms: float
    policy_version: str = POLICY_VERSION

    def data(self) -> dict:
        return {**asdict(self), "id": str(self.id), "detected_at": self.detected_at.isoformat()}


def activation(
    rule_id: str, category: str, replacement: str, latency_ms: float = 0
) -> SafetyActivation:
    return SafetyActivation(
        uuid4(), rule_id, category, replacement, datetime.now(UTC), latency_ms
    )


def check_sentence(text: str) -> tuple[str, FilterVerdict, tuple[SafetyActivation, ...]]:
    started = perf_counter()
    try:
        verdict = screen(text)
    except Exception:  # noqa: BLE001 — preserve the final fail-safe even if checker wiring breaks
        verdict = FilterVerdict(False, None, HARD_FAIL_LINE, "filter-error:unavailable", ())
    latency = max(0, (perf_counter() - started) * 1000)
    if verdict.allowed:
        return text, verdict, ()
    replacement = verdict.safe_line or HARD_FAIL_LINE
    categories = verdict.violations or ((verdict.category,) if verdict.category else ())
    events = tuple(
        activation(f"speech.{cat.value.lower()}.v2", cat.value, replacement, latency)
        for cat in categories
    )
    if not events:
        events = (activation("speech.filter_error.v2", "FILTER_ERROR", replacement, latency),)
    return replacement, verdict, events
