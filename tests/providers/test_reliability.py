import asyncio

import pytest
from roma.providers.reliability import (
    CircuitBreaker,
    FailureKind,
    ProviderFailure,
    provider_budget,
    remaining_timeout,
)


def test_breaker_opens_then_allows_only_one_recovery_probe():
    clock = [0.0]
    breaker = CircuitBreaker(threshold=2, reset_secs=5, clock=lambda: clock[0])
    failure = ProviderFailure(FailureKind.TRANSIENT)
    breaker.failure(breaker.enter(), failure)
    breaker.failure(breaker.enter(), failure)
    with pytest.raises(ProviderFailure, match="circuit_open"):
        breaker.enter()
    clock[0] = 5
    probe = breaker.enter()
    with pytest.raises(ProviderFailure, match="circuit_open"):
        breaker.enter()
    breaker.success(probe)
    breaker.enter()


def test_authentication_and_invalid_requests_do_not_trip_transient_breaker():
    breaker = CircuitBreaker(threshold=1)
    for kind in (FailureKind.AUTH, FailureKind.INVALID_REQUEST, FailureKind.QUOTA):
        breaker.failure(breaker.enter(), ProviderFailure(kind))
    breaker.enter()


def test_cancelling_recovery_probe_does_not_leave_breaker_permanently_busy():
    clock = [0.0]
    breaker = CircuitBreaker(threshold=1, reset_secs=1, clock=lambda: clock[0])
    breaker.failure(breaker.enter(), ProviderFailure(FailureKind.TIMEOUT))
    clock[0] = 2
    probe = breaker.enter()
    breaker.abandon(probe)
    breaker.success(breaker.enter())
    breaker.enter()


def test_late_success_cannot_close_a_newer_open_circuit():
    breaker = CircuitBreaker(threshold=1)
    old, current = breaker.enter(), breaker.enter()
    breaker.failure(current, ProviderFailure(FailureKind.TRANSIENT))
    breaker.success(old)
    with pytest.raises(ProviderFailure):
        breaker.enter()


def test_nested_provider_budgets_cannot_extend_parent_deadline():
    async def run():
        with provider_budget(0.03):
            with provider_budget(10):
                assert 0 < remaining_timeout(20) <= 0.03
            await asyncio.sleep(0.04)
            assert remaining_timeout(20) == 0
        assert remaining_timeout(2) == 2
    asyncio.run(run())


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True])
def test_invalid_provider_budgets_are_rejected(seconds):
    with pytest.raises(ValueError):
        with provider_budget(seconds):
            pass
