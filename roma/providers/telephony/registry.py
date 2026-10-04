"""Config-driven telephony provider selection."""

from __future__ import annotations

from roma.providers.telephony.base import PlaceCallRequest, PlaceCallResult, TelephonyProvider
from roma.providers.telephony.mock import MockTelephonyProvider
from roma.providers.telephony.twilio.client import build_twilio_client


class TwilioTelephonyProvider:
    def __init__(self, *, client, from_number: str) -> None:
        self._client = client
        self._from_number = from_number

    async def place_call(self, request: PlaceCallRequest) -> PlaceCallResult:
        call = self._client.calls.create(
            to=request.to_number,
            from_=request.from_number or self._from_number,
            url=request.answer_url,
            method="POST",
        )
        call_sid = getattr(call, "sid", None)
        return PlaceCallResult(provider_call_id=str(call_sid) if call_sid else None, status="queued")


def build_telephony_provider(settings) -> TelephonyProvider:
    match settings.telephony_provider:
        case "mock":
            return MockTelephonyProvider()
        case "twilio":
            return TwilioTelephonyProvider(
                client=build_twilio_client(),
                from_number=settings.twilio_from_number.get_secret_value(),
            )
    raise ValueError(f"unknown telephony provider {settings.telephony_provider!r}")


__all__ = ["TwilioTelephonyProvider", "build_telephony_provider"]
