"""Deterministic telephony adapter for tests and offline flows."""

from __future__ import annotations

from dataclasses import dataclass, field

from roma.providers.telephony.base import PlaceCallRequest, PlaceCallResult


@dataclass
class MockTelephonyProvider:
    provider_call_id: str = "CA_mock"
    placed: list[PlaceCallRequest] = field(default_factory=list)

    async def place_call(self, request: PlaceCallRequest) -> PlaceCallResult:
        self.placed.append(request)
        return PlaceCallResult(provider_call_id=self.provider_call_id, status="queued")


__all__ = ["MockTelephonyProvider"]
