"""PII-free provider failures, inherited deadlines and bounded circuit admission.

Retries belong to adapters which know whether any output or external effect escaped.
Native inference and carrier call creation are never implicitly repeated here.
"""

import asyncio
import math
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

from roma.core.ai_contracts import ProviderUnavailable


class FailureKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    AUTH = "authentication"
    INVALID_REQUEST = "invalid_request"
    QUOTA = "quota"
    UNAVAILABLE = "unavailable"
    CIRCUIT_OPEN = "circuit_open"


class ProviderFailure(ProviderUnavailable):
    def __init__(self, kind: FailureKind):
        self.kind = FailureKind(kind)
        super().__init__(f"Provider failure: {self.kind.value}")

    @property
    def retryable(self):
        return self.kind in {FailureKind.TIMEOUT, FailureKind.RATE_LIMIT, FailureKind.TRANSIENT}


_deadline: ContextVar[float | None] = ContextVar("roma_provider_deadline", default=None)


def _duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Provider duration must be finite and positive")
    return value


@contextmanager
def provider_budget(seconds):
    deadline = monotonic() + _duration(seconds)
    parent = _deadline.get()
    token = _deadline.set(min(deadline, parent) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_timeout(limit):
    limit = _duration(limit)
    deadline = _deadline.get()
    return min(limit, max(0.0, deadline - monotonic())) if deadline is not None else limit


@dataclass(frozen=True)
class _Permit:
    generation: int
    probe: bool


class CircuitBreaker:
    """Per-adapter protection; one recovery probe, no background timer or global state."""

    def __init__(self, *, threshold=3, reset_secs=15, clock=monotonic):
        if type(threshold) is not int or not 1 <= threshold <= 20:
            raise ValueError("Circuit threshold must be 1..20")
        self.threshold, self.reset_secs, self.clock = threshold, _duration(reset_secs), clock
        self._failures = 0
        self._opened = None
        self._probe = False
        self._generation = 0

    def enter(self):
        probe = self._opened is not None
        if probe:
            if self._probe or self.clock() - self._opened < self.reset_secs:
                raise ProviderFailure(FailureKind.CIRCUIT_OPEN)
            self._probe = True
        return _Permit(self._generation, probe)

    def success(self, permit):
        if permit.generation != self._generation:
            return
        self._failures, self._opened, self._probe = 0, None, False

    def failure(self, permit, failure):
        if permit.generation != self._generation:
            return
        if not failure.retryable and failure.kind != FailureKind.UNAVAILABLE:
            self.success(permit)
            return
        self._failures += 1
        if permit.probe or self._failures >= self.threshold:
            self._opened = self.clock()
            self._generation += 1
            self._probe = False

    def abandon(self, permit):
        if permit.generation == self._generation and permit.probe:
            self._probe = False


@contextmanager
def llm_turn_budget(provider):
    """Share configured Claude time across extraction/wording, including thin wrappers."""
    for _ in range(8):
        settings = getattr(provider, "settings", None)
        duration = getattr(settings, "anthropic_turn_timeout_secs", None)
        if duration is not None:
            with provider_budget(duration):
                yield
            return
        provider = getattr(provider, "provider", None)
        if provider is None:
            break
    yield  # Mocks and existing non-Claude providers keep their current deadlines.


async def bounded_stream(stream, seconds):
    """Bound provider pulls, never the downstream work performed after a yield."""
    deadline = monotonic() + remaining_timeout(seconds)
    async with aclosing(stream):
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            with provider_budget(remaining):
                async with asyncio.timeout(remaining):
                    try:
                        chunk = await anext(stream)
                    except StopAsyncIteration:
                        return
            yield chunk
