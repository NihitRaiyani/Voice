"""Telephony provider contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PlaceCallRequest:
    to_number: str
    from_number: str
    answer_url: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlaceCallResult:
    provider_call_id: str | None
    status: str


@runtime_checkable
class TelephonyProvider(Protocol):
    async def place_call(self, request: PlaceCallRequest) -> PlaceCallResult: ...


__all__ = ["PlaceCallRequest", "PlaceCallResult", "TelephonyProvider"]
