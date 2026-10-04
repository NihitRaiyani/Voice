from __future__ import annotations

import asyncio

from roma.core.config import Settings
from roma.providers.telephony import (
    MockTelephonyProvider,
    PlaceCallRequest,
    TelephonyProvider,
    build_telephony_provider,
)


def _settings(**overrides) -> Settings:
    values = {
        "sarvam_api_key": "sarvam-test",
        "openai_api_key": "openai-test",
        "redis_url": "redis://localhost:6379/0",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_mock_telephony_records_calls_without_touching_twilio():
    async def run() -> None:
        provider = build_telephony_provider(_settings(telephony_provider="mock"))
        assert isinstance(provider, MockTelephonyProvider)
        assert isinstance(provider, TelephonyProvider)
        result = await provider.place_call(
            PlaceCallRequest(
                to_number="+919876543210",
                from_number="+16295550100",
                answer_url="https://roma.example.com/answer",
            )
        )
        assert result.provider_call_id == "CA_mock"
        assert result.status == "queued"
        assert provider.placed[0].to_number == "+919876543210"

    asyncio.run(run())
