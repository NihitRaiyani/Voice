"""Synthetic in-memory Claude SDK requests; never contact a model endpoint."""

import asyncio
import json

import httpx
import pytest
from anthropic import AsyncAnthropic
from roma.core.ai_contracts import ProviderUnavailable
from roma.providers.jobs.claude_summary import ClaudeSummary
from tests.providers.ai.test_claude_llm import settings, sse


def test_summary_requires_explicit_paid_opt_in_before_client_creation():
    with pytest.raises(ProviderUnavailable, match="explicit"):
        ClaudeSummary(settings())


def test_summary_uses_separate_bounded_claude_request_and_only_call_metadata():
    async def run():
        seen = []

        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(
                200,
                text=sse("Call ended without a captured visit time."),
                headers={"content-type": "text/event-stream"},
            )

        client = AsyncAnthropic(
            api_key="offline-only",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        summary = ClaudeSummary(settings(), allow_paid_api=True, client=client)
        try:
            result = await summary.summarize(
                {
                    "final_stage": "close",
                    "outcome": "no_lock",
                    "duration_secs": 240,
                    "audio_secs": 120,
                    "turn_count": 8,
                    "transcript": "PRIVATE should never be sent",
                    "phone": "+919876543210",
                }
            )
            assert result["source"] == "claude_call_metadata"
            assert result["text"] == "Call ended without a captured visit time."
            assert result["output_tokens"] == 2
            assert result["charged_inr"] <= 0.25
            assert len(seen) == 1
            payload = seen[0]
            assert payload["max_tokens"] == 128
            assert payload["model"] == "claude-haiku-5-5"
            assert "PRIVATE" not in json.dumps(payload)
            assert "+919876543210" not in json.dumps(payload)
            assert summary.settings.anthropic_max_retries == 0
            assert summary.settings.anthropic_call_token_budget == 2048
            assert summary.settings.anthropic_call_spend_cap_inr == 0.25
        finally:
            await client.close()

    asyncio.run(run())


def test_unmetered_or_truncated_summary_is_not_acknowledged_as_success():
    async def run():
        for response in (sse(usage=False), sse(stop_reason="max_tokens")):
            client = AsyncAnthropic(
                api_key="offline-only",
                http_client=httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request, body=response: httpx.Response(
                            200, text=body, headers={"content-type": "text/event-stream"}
                        )
                    )
                ),
            )
            try:
                summary = ClaudeSummary(settings(), allow_paid_api=True, client=client)
                with pytest.raises(ProviderUnavailable):
                    await summary.summarize({"final_stage": "close", "outcome": "no_lock"})
            finally:
                await client.close()

    asyncio.run(run())
