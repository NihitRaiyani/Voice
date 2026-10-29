"""Offline Claude SDK integration: synthetic SSE through an in-memory HTTP transport."""

import asyncio
import json
import math
import time
from dataclasses import replace

import httpx
import pytest
from anthropic import AsyncAnthropic
from roma.core.claude_llm_settings import ClaudeLLMSettings
from roma.domain.costs.spend import USD_TO_INR
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.ai.claude_llm import ClaudeLLM
from roma.providers.ai.contracts import LLMMessage, LLMRequest, ProviderUnavailable


def settings(**values):
    defaults = {
        "llm_provider": "claude",
        "anthropic_api_key": "offline-secret",
        "anthropic_retry_backoff_secs": 0,
    }
    return ClaudeLLMSettings(_env_file=None, **(defaults | values))


REQUEST = LLMRequest(
    (LLMMessage("system", "Be concise."), LLMMessage("user", "hello")), max_tokens=16
)


def event(name, data):
    return f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"


def sse(
    text="Hello",
    *,
    usage=True,
    stop_reason="end_turn",
    extra_blocks=False,
    input_tokens=20,
    cache_read=5,
    cache_write=3,
    output_tokens=2,
):
    initial_usage = {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": 0,
    }
    if cache_write is None:
        initial_usage.pop("cache_creation_input_tokens")
    message = {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
    }
    if usage:
        message["usage"] = initial_usage
    rows = [event("message_start", {"message": message})]
    index = 0
    if extra_blocks:
        rows.extend(
            [
                event(
                    "content_block_start",
                    {
                        "index": index,
                        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                    },
                ),
                event(
                    "content_block_delta",
                    {
                        "index": index,
                        "delta": {"type": "thinking_delta", "thinking": "private-reasoning"},
                    },
                ),
                event("content_block_stop", {"index": index}),
            ]
        )
        index += 1
        rows.extend(
            [
                event(
                    "content_block_start",
                    {
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": "tool_synthetic",
                            "name": "private-tool",
                            "input": {},
                        },
                    },
                ),
                event(
                    "content_block_delta",
                    {
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"secret":"tool-payload"}',
                        },
                    },
                ),
                event("content_block_stop", {"index": index}),
            ]
        )
        index += 1
    rows.extend(
        [
            event(
                "content_block_start",
                {"index": index, "content_block": {"type": "text", "text": ""}},
            ),
            event(
                "content_block_delta",
                {"index": index, "delta": {"type": "text_delta", "text": text}},
            ),
            event("content_block_stop", {"index": index}),
            event(
                "message_delta",
                {
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    **({"usage": {"output_tokens": output_tokens}} if usage else {}),
                },
            ),
            event("message_stop", {}),
        ]
    )
    return "".join(rows)


def response(**kw):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(**kw))


async def exercise(handler, cfg=None, request=REQUEST, sdk_retries=0):
    client = AsyncAnthropic(
        api_key="offline-secret",
        max_retries=sdk_retries,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        provider = ClaudeLLM(cfg or settings(), client=client)
        chunks = [chunk async for chunk in provider.generate(request)]
        return provider, chunks
    finally:
        await client.close()


@pytest.mark.parametrize("stop_reason,finish", [("end_turn", "stop"), ("max_tokens", "length")])
def test_sdk_stream_roles_low_effort_cache_and_normalized_usage(stop_reason, finish):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return response(stop_reason=stop_reason)

    provider, chunks = asyncio.run(exercise(handler))
    assert "".join(chunk.text for chunk in chunks) == "Hello"
    assert chunks[-1].finish_reason == finish
    metrics = chunks[-1].metrics
    assert metrics.usage_known and metrics.input_tokens == 28
    assert metrics.cached_input_tokens == 5 and metrics.cache_creation_input_tokens == 3
    assert metrics.output_tokens == 2 and provider.tokens_used == 30
    assert metrics.ttft_ms is not None and metrics.cost_inr > 0
    payload = calls[0]
    assert payload["model"] == "claude-haiku-5-5" and payload["max_tokens"] == 16
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["system"] == [
        {"type": "text", "text": "Be concise.", "cache_control": {"type": "ephemeral"}}
    ]
    assert payload["output_config"]["effort"] == "low"
    assert payload["thinking"] == {"type": "disabled"}
    assert not {"temperature", "top_p", "top_k", "metadata"} & payload.keys()


def test_cache_write_read_and_output_are_priced_once():
    provider, chunks = asyncio.run(
        exercise(
            lambda req: response(
                input_tokens=10000, cache_read=20000, cache_write=30000, output_tokens=512
            ),
            settings(anthropic_call_token_budget=100000, anthropic_max_output_tokens=512),
            request=replace(REQUEST, max_tokens=512),
        )
    )
    metrics = chunks[-1].metrics
    expected = (
        math.ceil(
            (10000 * 0.10 + 20000 * 0.01 + 30000 * 0.125 + 512 * 0.50)
            / 1000000
            * USD_TO_INR
            * 100
        )
        / 100
    )
    assert metrics.input_tokens == 60000 and metrics.output_tokens == 512
    assert provider.tokens_used == 60512 and metrics.cost_inr == expected


def test_extraction_schema_and_prompt_privacy_do_not_leak_metadata():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return response(text='{"value":"working","confidence":0.9}')

    request = replace(
        REQUEST,
        messages=(
            LLMMessage("system", "Extract bounded JSON."),
            LLMMessage(
                "user", "+919123456789 x@y.com " + "f" * 64 + " https://recording.invalid/a.wav"
            ),
        ),
        metadata={"purpose": "extraction", "call_sid": "secret-call"},
        response_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    _, chunks = asyncio.run(exercise(handler, request=request))
    payload = calls[0]
    assert payload["output_config"]["format"]["type"] == "json_schema"
    assert payload["output_config"]["format"]["schema"]["type"] == "object"
    assert chunks[-1].metrics.usage_known
    for private in ("919123456789", "x@y.com", "f" * 64, "recording.invalid", "secret-call"):
        assert private not in str(payload)
    assert "metadata" not in payload


def test_reasoning_and_tool_delta_never_become_spoken_text():
    _, chunks = asyncio.run(exercise(lambda req: response(extra_blocks=True)))
    spoken = "".join(c.text for c in chunks)
    assert "private-reasoning" not in spoken and "tool-payload" not in spoken
    assert "private-tool" not in spoken


@pytest.mark.parametrize(
    "status,category,retry",
    [
        (429, "rate_limit_error", True),
        (503, "overloaded_error", True),
        (429, "insufficient_quota", False),
        (429, "billing_error", False),
        (401, "authentication_error", False),
        (400, "invalid_request_error", False),
    ],
)
def test_transient_retry_is_bounded_and_never_exposes_raw_error(
    status, category, retry, caplog
):
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(
                status,
                json={
                    "type": "error",
                    "error": {"type": category, "message": "secret-error-body offline-secret"},
                },
            )
        return response()

    _, chunks = asyncio.run(exercise(handler, sdk_retries=7))
    assert len(calls) == (2 if retry else 1)
    assert chunks[-1].metrics.attempts == len(calls)
    if retry:
        assert chunks[-1].finish_reason == "stop"
        assert chunks[-1].metrics.call_tokens_used > 30
    else:
        assert chunks[-1].text == HARD_FAIL_LINE
        assert chunks[-1].metrics.error_code == "provider_unavailable"
    assert "secret-error-body" not in str(chunks) + caplog.text
    assert "offline-secret" not in str(chunks) + caplog.text


def test_retry_after_is_honored_and_cannot_exceed_total_deadline():
    async def run(deadline, retry_after):
        calls = []

        def handler(req):
            calls.append(time.monotonic())
            if len(calls) == 1:
                return httpx.Response(
                    429,
                    headers={"retry-after": str(retry_after)},
                    json={
                        "type": "error",
                        "error": {"type": "rate_limit_error", "message": "busy"},
                    },
                )
            return response()

        provider, chunks = await exercise(
            handler, settings(anthropic_turn_timeout_secs=deadline)
        )
        assert provider.tokens_used > 0
        return calls, chunks

    calls, chunks = asyncio.run(run(1, 0.03))
    assert len(calls) == 2 and calls[1] - calls[0] >= 0.025
    assert chunks[-1].finish_reason == "stop"
    started = time.monotonic()
    calls, chunks = asyncio.run(run(0.02, 1))
    assert time.monotonic() - started < 0.5
    assert len(calls) == 1 and chunks[-1].finish_reason == "provider_unavailable"


@pytest.mark.parametrize(
    "values",
    [
        {"anthropic_call_token_budget": 1},
        {"anthropic_call_spend_cap_inr": 0.001},
    ],
)
def test_token_and_spend_budget_block_before_http(values):
    calls = []

    def handler(req):
        calls.append(req)
        return response()

    _, chunks = asyncio.run(exercise(handler, settings(**values)))
    assert not calls and chunks[-1].finish_reason == "budget_exhausted"
    assert chunks[-1].metrics.attempts == 0


def test_missing_usage_retains_reservation_fail_closed():
    provider, chunks = asyncio.run(exercise(lambda req: response(usage=False)))
    assert not chunks[-1].metrics.usage_known and chunks[-1].metrics.cost_inr is None
    assert provider.tokens_used > 30 and chunks[-1].metrics.charged_inr > 0


def test_model_override_and_oversized_prompt_reject_before_http():
    async def run():
        provider = ClaudeLLM(settings(anthropic_max_prompt_bytes=256), client=object())
        for request in (
            replace(REQUEST, model="unpriced-model"),
            replace(REQUEST, messages=(LLMMessage("user", "x" * 300),)),
        ):
            with pytest.raises(ProviderUnavailable):
                async for _ in provider.generate(request):
                    pass

    asyncio.run(run())


class InterruptedStream(httpx.AsyncByteStream):
    def __init__(self, *, fail=False):
        self.closed = False
        self.sent = asyncio.Event()
        self.fail = fail

    async def __aiter__(self):
        prefix = sse(text="partial").split("event: content_block_stop")[0]
        yield prefix.encode()
        self.sent.set()
        if self.fail:
            raise httpx.ReadError("secret-stream-error")
        await asyncio.sleep(10)

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("mode", ["timeout", "cancel", "failure"])
def test_partial_text_never_retries_and_timeout_cancel_close_transport(mode):
    async def run():
        stream = InterruptedStream(fail=mode == "failure")
        calls = []

        def handler(req):
            calls.append(req)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )

        client = AsyncAnthropic(
            api_key="offline",
            max_retries=7,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        provider = ClaudeLLM(settings(anthropic_attempt_timeout_secs=0.03), client=client)
        chunks = []

        async def collect():
            async for chunk in provider.generate(REQUEST):
                chunks.append(chunk)

        try:
            if mode == "cancel":
                task = asyncio.create_task(collect())
                await asyncio.wait_for(stream.sent.wait(), timeout=1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await collect()
                assert chunks[0].text == "partial"
                assert chunks[-1].text == HARD_FAIL_LINE
                assert chunks[-1].metrics.input_tokens is None
            assert stream.closed and len(calls) == 1 and provider.tokens_used > 0
            assert "secret-stream-error" not in str(chunks)
        finally:
            await client.close()

    asyncio.run(run())


def test_absent_cache_creation_count_defaults_to_zero():
    provider, chunks = asyncio.run(exercise(lambda req: response(cache_write=None)))
    metrics = chunks[-1].metrics
    assert metrics.usage_known and metrics.cache_creation_input_tokens == 0
    assert metrics.input_tokens == 25 and provider.tokens_used == 27


def test_cache_can_be_disabled_without_changing_system_text():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return response()

    asyncio.run(exercise(handler, settings(anthropic_prompt_cache=False)))
    assert calls[0]["system"] == [{"type": "text", "text": "Be concise."}]


def test_zero_output_usage_for_real_text_cannot_refund_reservation():
    provider, chunks = asyncio.run(exercise(lambda req: response(output_tokens=0)))
    assert chunks[-1].finish_reason == "provider_unavailable"
    assert not chunks[-1].metrics.usage_known and provider.tokens_used > 30


def test_pydantic_constraints_are_sdk_transformed_and_still_validated_in_application():
    from roma.services.text_conversation_service import ValidatedDiscoveryValue

    payload = []
    request = replace(REQUEST, response_schema=ValidatedDiscoveryValue.model_json_schema())

    def handler(req):
        payload.append(json.loads(req.content))
        return response(text='{"value":"student","confidence":0.9}')

    _, chunks = asyncio.run(exercise(handler, request=request))
    wire = payload[0]["output_config"]["format"]["schema"]
    confidence = wire["properties"]["confidence"]
    assert "minimum" not in confidence and "maximum" not in confidence
    assert "minimum" in confidence.get("description", "")
    assert (
        ValidatedDiscoveryValue.model_validate_json("".join(c.text for c in chunks)).value
        == "student"
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ValidatedDiscoveryValue.model_validate_json('{"value":"student","confidence":2}')


def test_empty_completion_is_a_safe_failure_with_actual_usage_not_false_success():
    _, chunks = asyncio.run(exercise(lambda req: response(text="", stop_reason="max_tokens")))
    assert chunks[-1].text == HARD_FAIL_LINE
    assert chunks[-1].finish_reason == "provider_unavailable"
    assert chunks[-1].metrics.error_code == "empty_output"
    assert chunks[-1].metrics.usage_known and chunks[-1].metrics.ttft_ms is None
    assert chunks[-1].metrics.call_tokens_used == 30


def test_repeated_transient_failures_open_circuit_without_http_or_extra_charges():
    from roma.providers.reliability import CircuitBreaker

    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={"type": "error", "error": {"type": "overloaded_error", "message": "private"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AsyncAnthropic(api_key="offline-secret", http_client=http)
            provider = ClaudeLLM(settings(anthropic_max_retries=0), client=client,
                                 circuit=CircuitBreaker(threshold=2, reset_secs=1))
            for _ in range(2):
                assert [c async for c in provider.generate(REQUEST)][-1].text == HARD_FAIL_LINE
            charged = provider.spent_cents
            blocked = [c async for c in provider.generate(REQUEST)]
            assert len(calls) == 2 and provider.spent_cents == charged
            assert blocked[-1].text == HARD_FAIL_LINE
            assert blocked[-1].metrics.attempts == 0
    asyncio.run(run())


def test_parent_budget_covers_lock_admission_and_prevents_http_after_expiry():
    from roma.providers.reliability import provider_budget

    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = ClaudeLLM(settings(), client=AsyncAnthropic(api_key="offline-secret", http_client=http))
            await provider._lock.acquire()
            try:
                with provider_budget(0.02):
                    chunks = await asyncio.wait_for(_collect(provider), 0.2)
                assert not calls and chunks[-1].text == HARD_FAIL_LINE
                assert chunks[-1].metrics.attempts == 0
            finally:
                provider._lock.release()
    asyncio.run(run())


async def _collect(provider):
    return [chunk async for chunk in provider.generate(REQUEST)]


def test_overload_sse_before_first_text_can_retry_but_not_after_text():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=event("error", {"error": {"type": "overloaded_error", "message": "private"}}))
        return response()
    _, chunks = asyncio.run(exercise(handler))
    assert len(calls) == 2 and chunks[-1].finish_reason == "stop"


def test_expired_parent_budget_never_reserves_or_sends_a_request():
    from roma.providers.reliability import provider_budget

    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = ClaudeLLM(settings(), client=AsyncAnthropic(api_key="offline-secret", http_client=http))
            with provider_budget(0.01):
                await asyncio.sleep(0.02)
                chunks = await _collect(provider)
            assert not calls and provider.spent_cents == 0
            assert chunks[-1].metrics.attempts == 0
    asyncio.run(run())


def test_parent_deadline_during_request_keeps_attempt_and_conservative_charge():
    from roma.providers.reliability import provider_budget

    async def run():
        async def handler(request):
            await asyncio.sleep(10)
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = ClaudeLLM(settings(), client=AsyncAnthropic(api_key="offline-secret", http_client=http))
            with provider_budget(0.02):
                chunks = await _collect(provider)
            assert chunks[-1].metrics.attempts == 1
            assert chunks[-1].metrics.charged_inr == provider.spent_cents / 100 > 0
    asyncio.run(run())


def test_closing_after_a_yield_immediately_closes_underlying_stream():
    async def run():
        stream = InterruptedStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))) as http:
            provider = ClaudeLLM(settings(), client=AsyncAnthropic(api_key="offline-secret", http_client=http))
            generation = provider.generate(REQUEST)
            assert (await anext(generation)).text == "partial"
            await generation.aclose()
            assert stream.closed
    asyncio.run(run())


def test_cancellation_before_first_token_never_retries_or_trips_circuit():
    from roma.providers.reliability import CircuitBreaker

    async def run():
        calls, started = [], asyncio.Event()
        async def handler(request):
            calls.append(request)
            if len(calls) == 1:
                started.set()
                await asyncio.Event().wait()
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = ClaudeLLM(settings(), client=AsyncAnthropic(api_key="offline-secret", http_client=http),
                                 circuit=CircuitBreaker(threshold=1))
            task = asyncio.create_task(_collect(provider))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(calls) == 1
            assert (await _collect(provider))[-1].finish_reason == "stop"
            assert len(calls) == 2
    asyncio.run(run())


def test_claude_budget_does_not_cancel_downstream_consumer_between_pulls():
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = ClaudeLLM(settings(anthropic_turn_timeout_secs=0.02), client=AsyncAnthropic(api_key="offline-secret", http_client=http))
            generation = provider.generate(REQUEST)
            try:
                assert (await anext(generation)).text == "Hello"
                await asyncio.sleep(0.08)  # downstream playback/backpressure owns this wait
                tail = [chunk async for chunk in generation]
                assert tail[-1].text == HARD_FAIL_LINE and len(calls) == 1
            finally:
                await generation.aclose()
    asyncio.run(run())



def test_cold_schema_and_client_setup_keep_the_live_event_loop_responsive(monkeypatch):
    import threading

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response())) as http:
            client = AsyncAnthropic(api_key='offline-secret', http_client=http)
            provider = ClaudeLLM(settings(), client=client)
            setup_threads, beats = [], []
            for name in ('_payload', '_get_client'):
                original = getattr(provider, name)
                def slow(*args, original=original):
                    setup_threads.append(threading.get_ident())
                    time.sleep(.06)  # Synthetic import/schema/client CPU preparation.
                    return original(*args)
                monkeypatch.setattr(provider, name, slow)
            async def heartbeat():
                while True:
                    beats.append(time.monotonic())
                    await asyncio.sleep(.005)
            monitor = asyncio.create_task(heartbeat())
            task = asyncio.create_task(collect_chunks(provider))
            try:
                await asyncio.sleep(.025)
                assert not task.done() and len(beats) >= 3
                chunks = await task
                assert chunks[-1].metrics.error_code is None
                assert all(ident != threading.get_ident() for ident in setup_threads)
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, task, return_exceptions=True)
                await client.close()
    asyncio.run(run())


async def collect_chunks(provider):
    return [chunk async for chunk in provider.generate(REQUEST)]


@pytest.mark.parametrize('partial', [False, True])
def test_sdk_and_raw_stream_timeouts_keep_sanitized_timeout_metrics_without_partial_retry(partial):
    class RawTimeout(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse(text='partial').split('event: content_block_stop')[0].encode()
            raise httpx.ReadTimeout('private-timeout-body')
        async def aclose(self):
            pass
    calls = []
    def handler(request):
        calls.append(request)
        if partial:
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=RawTimeout())
        raise httpx.ReadTimeout('private-timeout-body', request=request)
    provider, chunks = asyncio.run(exercise(handler, cfg=settings(anthropic_max_retries=1 if partial else 0)))
    assert len(calls) == 1
    assert chunks[-1].metrics.error_code == chunks[-1].finish_reason == 'provider_timeout'
    assert provider._last_failure.kind.value == 'timeout'
    assert 'private-timeout-body' not in str(chunks)



def test_cancelled_setup_retains_single_worker_admission_without_sending_a_request(monkeypatch):
    import threading

    async def run():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        calls, preparations = [], []
        def handler(request):
            calls.append(request)
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AsyncAnthropic(api_key='offline-secret', http_client=http)
            provider = ClaudeLLM(settings(), client=client)
            original = provider._payload
            def blocked(request):
                preparations.append(1)
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(2)
                return original(request)
            monkeypatch.setattr(provider, '_payload', blocked)
            task = asyncio.create_task(collect_chunks(provider))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, .2)
                successor = await collect_chunks(provider)
                assert successor[-1].metrics.attempts == 0
                assert successor[-1].metrics.error_code == 'provider_unavailable'
                assert preparations == [1] and calls == [] and provider.tokens_used == 0
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await asyncio.sleep(.02)
                await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['cancel', 'timeout'])
def test_late_unused_sdk_client_is_closed_after_setup_withdrawal_without_api_io(monkeypatch, mode):
    import threading

    async def run():
        entered, release, client_closed = asyncio.Event(), threading.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        class Client:
            async def close(self):
                client_closed.set()
        client = Client()
        provider = ClaudeLLM(settings(anthropic_attempt_timeout_secs=.025))
        def create():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return client
        monkeypatch.setattr(provider, '_get_client', create)
        task = asyncio.create_task(collect_chunks(provider))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if mode == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, .2)
            else:
                chunks = await asyncio.wait_for(task, .2)
                assert chunks[-1].metrics.error_code == 'provider_timeout'
                assert chunks[-1].metrics.attempts == 0
            await asyncio.wait_for(provider.aclose(), .2)
            assert provider._client is None and provider.tokens_used == 0
            release.set()
            await asyncio.wait_for(client_closed.wait(), .5)
            assert provider._client is None
            with pytest.raises(ProviderUnavailable, match='closed'):
                await collect_chunks(provider)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())



def test_cancelled_setup_keeps_admission_until_publication_and_drains_scheduled_unused_close(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        ready, closed = asyncio.Event(), asyncio.Event()
        deferred, clients = [], []
        original = loop.call_soon_threadsafe
        class Client:
            async def close(self):
                await asyncio.sleep(.01)
                closed.set()
        def create():
            client = Client()
            clients.append(client)
            return client
        def delay_publication(callback, *args, **kwargs):
            if getattr(callback, '__name__', '') == 'deliver':
                deferred.append((callback, args))
                original(ready.set)
                return
            return original(callback, *args, **kwargs)
        provider = ClaudeLLM(settings())
        monkeypatch.setattr(provider, '_get_client', create)
        monkeypatch.setattr(loop, 'call_soon_threadsafe', delay_publication)
        task = asyncio.create_task(collect_chunks(provider))
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        successor = asyncio.create_task(collect_chunks(provider))
        await asyncio.sleep(.01)
        assert len(clients) == 1
        assert successor.done() and successor.result()[-1].metrics.attempts == 0
        callback, args = deferred.pop()
        callback(*args)
        await provider.aclose()
        assert closed.is_set() and not provider._late_client_closes
        assert provider._client is None
    asyncio.run(run())



def test_closing_cached_client_during_payload_setup_cannot_create_an_unowned_replacement(monkeypatch):
    import threading

    async def run():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        replacements = []
        class Client:
            def __init__(self):
                self.close_count = 0
            async def close(self):
                self.close_count += 1
        cached = Client()
        provider = ClaudeLLM(settings())
        provider._client = cached
        original_payload = provider._payload
        def payload(request):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return original_payload(request)
        def get_client():
            if provider._client is not None:
                return provider._client
            client = Client()
            replacements.append(client)
            return client
        monkeypatch.setattr(provider, '_payload', payload)
        monkeypatch.setattr(provider, '_get_client', get_client)
        task = asyncio.create_task(collect_chunks(provider))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(provider.aclose(), .2)
            release.set()
            with pytest.raises(ProviderUnavailable, match='closed'):
                await asyncio.wait_for(task, .5)
            assert cached.close_count == 1 and provider._client is None
            assert replacements == []
            assert not provider._late_client_closes and provider.tokens_used == 0
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
