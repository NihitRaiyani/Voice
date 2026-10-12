import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from roma.core.openai_llm_settings import OpenAILLMSettings
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.ai.contracts import LLMMessage, LLMRequest, ProviderUnavailable
from roma.providers.ai.openai_llm import OpenAILLM


def settings(**values):
    return OpenAILLMSettings(
        _env_file=None,
        openai_api_key="offline-secret",
        openai_model="gpt-4o",
        openai_retry_backoff_secs=0,
        **values,
    )


REQUEST = LLMRequest(
    (LLMMessage("system", "Use JSON only."), LLMMessage("user", "hello")), max_tokens=16
)


def response(text="Hello", usage=True):
    rows = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    if usage:
        rows.append(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 2,
                    "total_tokens": 22,
                    "prompt_tokens_details": {"cached_tokens": 5},
                },
            }
        )
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join("data: " + json.dumps(r) + "\n\n" for r in rows) + "data: [DONE]\n\n",
    )


async def exercise(handler, cfg=None, request=REQUEST):
    client = AsyncOpenAI(
        api_key="offline-secret",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        provider = OpenAILLM(cfg or settings(), client=client)
        chunks = [c async for c in provider.generate(request)]
        return provider, chunks
    finally:
        await client.close()


def test_real_sdk_stream_translation_usage_only_event_and_payload_privacy():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return response()

    req = replace(
        REQUEST,
        messages=(
            LLMMessage(
                "user", "+919123456789 x@y.com " + "f" * 64 + " https://recording.invalid/a.wav"
            ),
        ),
        metadata={"purpose": "extraction", "call_sid": "secret-call"},
    )
    provider, chunks = asyncio.run(exercise(handler, request=req))
    assert "".join(c.text for c in chunks) == "Hello"
    assert chunks[-1].finish_reason == "stop"
    metrics = chunks[-1].metrics
    assert metrics.usage_known and metrics.input_tokens == 20 and metrics.output_tokens == 2
    assert metrics.cached_input_tokens == 5 and metrics.ttft_ms is not None
    assert metrics.cost_inr > 0 and provider.tokens_used == 22
    payload = calls[0]
    assert payload["model"] == "gpt-4o" and payload["max_completion_tokens"] == 16
    assert payload["stream_options"] == {"include_usage": True} and payload["store"] is False
    assert payload["response_format"] == {"type": "json_object"}
    assert "metadata" not in payload and "secret-call" not in str(payload)
    assert "919123456789" not in str(payload) and "recording.invalid" not in str(payload)
    assert "f" * 64 not in str(payload) and "x@y.com" not in str(payload)


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (429, "rate_limit_exceeded", True),
        (503, "unavailable", True),
        (429, "insufficient_quota", False),
        (429, "billing_hard_limit_reached", False),
        (401, "invalid_api_key", False),
        (400, "bad_request", False),
    ],
)
def test_only_transient_errors_retry_without_sdk_nested_retries(status, code, retry):
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(
                status,
                json={"error": {"message": "secret-error-body", "code": code, "type": code}},
            )
        return response()

    _, chunks = asyncio.run(exercise(handler))
    assert len(calls) == (2 if retry else 1)
    assert chunks[-1].metrics.attempts == len(calls)
    if retry:
        assert chunks[-1].finish_reason == "stop" and chunks[-1].metrics.call_tokens_used > 22
    else:
        assert chunks[-1].text == HARD_FAIL_LINE
        assert chunks[-1].metrics.error_code == "provider_unavailable"
        assert "secret-error-body" not in str(chunks)


@pytest.mark.parametrize(
    "kwargs", [{"openai_call_token_budget": 1}, {"openai_call_spend_cap_inr": 0.01}]
)
def test_budget_blocks_before_any_http_request(kwargs):
    calls = []

    def handler(req):
        calls.append(req)
        return response()

    _, chunks = asyncio.run(exercise(handler, settings(**kwargs)))
    assert calls == [] and chunks[-1].finish_reason == "budget_exhausted"
    assert chunks[-1].metrics.attempts == 0


def test_missing_usage_retains_reservation_instead_of_refunding_to_zero():
    provider, chunks = asyncio.run(exercise(lambda r: response(usage=False)))
    assert not chunks[-1].metrics.usage_known
    assert chunks[-1].metrics.cost_inr is None
    assert provider.tokens_used > 22 and chunks[-1].metrics.charged_inr > 0


def test_deadline_and_cancellation_close_stream_and_do_not_retry_partial_text():
    class Stream:
        def __init__(self):
            self.closed = False

        def __aiter__(self):
            return self.run()

        async def run(self):
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="partial"), finish_reason=None
                    )
                ],
            )
            await asyncio.sleep(1)

        async def close(self):
            self.closed = True

    async def run(cancel=False):
        stream = Stream()
        calls = []

        async def create(**kw):
            calls.append(kw)
            return stream

        provider = OpenAILLM(
            settings(openai_attempt_timeout_secs=0.02),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        )
        if cancel:

            async def collect():
                return [c async for c in provider.generate(REQUEST)]

            task = asyncio.create_task(collect())
            await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            chunks = [c async for c in provider.generate(REQUEST)]
            assert chunks[0].text == "partial" and chunks[-1].text == HARD_FAIL_LINE
            assert chunks[-1].metrics.input_tokens is None
        assert stream.closed and len(calls) == 1 and provider.tokens_used > 0

    asyncio.run(run())
    asyncio.run(run(True))


def test_invalid_model_and_oversize_prompt_fail_before_http():
    async def run():
        p = OpenAILLM(settings(openai_max_prompt_bytes=256), client=object())
        with pytest.raises(ProviderUnavailable):
            async for _ in p.generate(replace(REQUEST, model="different")):
                pass
        with pytest.raises(ProviderUnavailable):
            async for _ in p.generate(
                replace(REQUEST, messages=(LLMMessage("user", "x" * 300),))
            ):
                pass

    asyncio.run(run())


def test_injected_sdk_client_cannot_hide_automatic_retries():
    async def run():
        calls = []

        def handler(req):
            calls.append(req)
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": "quota",
                        "code": "insufficient_quota",
                        "type": "insufficient_quota",
                    }
                },
            )

        client = AsyncOpenAI(
            api_key="offline",
            max_retries=7,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            p = OpenAILLM(settings(), client=client)
            chunks = [c async for c in p.generate(REQUEST)]
            assert len(calls) == 1 and chunks[-1].text == HARD_FAIL_LINE
        finally:
            await client.close()

    asyncio.run(run())


def test_invalid_zero_usage_never_refunds_a_real_text_completion():
    def handler(req):
        reply = response()
        return httpx.Response(
            200,
            headers=reply.headers,
            text=reply.text.replace('"prompt_tokens": 20', '"prompt_tokens": 0'),
        )

    p, chunks = asyncio.run(exercise(handler))
    assert chunks[-1].finish_reason == "provider_unavailable"
    assert not chunks[-1].metrics.usage_known and p.tokens_used > 0


def test_total_deadline_includes_backoff_and_budget_survives_concurrent_turns():
    async def run():
        calls = []

        def handler(req):
            calls.append(req)
            return httpx.Response(
                503, json={"error": {"message": "down", "code": "unavailable"}}
            )

        cfg = settings(openai_turn_timeout_secs=0.01)
        cfg.openai_retry_backoff_secs = 0.2
        p, chunks = await exercise(handler, cfg)
        assert (
            len(calls) == 1
            and chunks[-1].finish_reason == "provider_timeout"
            and p.tokens_used > 0
        )
        client = AsyncOpenAI(
            api_key="offline",
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda req: response())
            ),
        )
        try:
            p = OpenAILLM(settings(), client=client)

            async def collect():
                return [c async for c in p.generate(REQUEST)]

            await asyncio.gather(collect(), collect())
            assert p.tokens_used == 44 and p.spent_cents > 0
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("stage", "caller", "reply"),
    [
        (
            "value",
            "Tell me practical skills",
            "Practical projects se portfolio banega. Aap kya seekhna chahenge?",
        ),
        ("discover", "I work", '{"value":"working","confidence":0.95}'),
        (
            "pivot",
            "kal teen baje aaunga",
            '{"day_offset":1,"hour":15,"accepted":true,"confidence":0.95}',
        ),
    ],
)
def test_default_budget_allows_a_normal_controller_turn_through_real_sdk_transport(
    stage, caller, reply
):
    from datetime import datetime

    from roma.domain.appointments.timeresolve import IST
    from roma.domain.conversation.state import CallState
    from roma.services.text_conversation_service import TextConversationService

    async def run():
        calls = []

        def handler(req):
            calls.append(json.loads(req.content))
            return response(text=reply)

        client = AsyncOpenAI(
            api_key="offline",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            state = CallState(call_sid="synthetic-integrated", stage=stage, lead_name="Amit")
            trace = await TextConversationService(OpenAILLM(settings(), client=client)).turn(
                state, caller, now=datetime(2026, 10, 12, 10, tzinfo=IST)
            )
            assert len(calls) == 1 and not trace.provider_error
            assert trace.metrics[0]["usage_known"] and not trace.booking_committed
            if stage == "pivot":
                assert state.accepted_slot == "2026-10-13T15:00:00+05:30"
            if stage == "discover":
                assert state.current_status == "working"
        finally:
            await client.close()

    asyncio.run(run())
