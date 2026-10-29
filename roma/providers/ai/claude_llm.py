"""Bounded Anthropic async streaming behind the transport-neutral LLMProvider.

Only text deltas cross this adapter. Stage, facts, slots and final speech safety
remain application-owned. Automatic SDK retries are disabled to bound one turn.
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack, aclosing
from email.utils import parsedate_to_datetime
from typing import Any

from roma.core.ai_contracts import (
    LLMChunk,
    LLMGenerationMetrics,
    LLMRequest,
    ProviderUnavailable,
)
from roma.core.prompt_privacy import redact_prompt
from roma.domain.costs.spend import Usage, inr_for
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.reliability import (
    CircuitBreaker,
    FailureKind,
    ProviderFailure,
    bounded_stream,
    remaining_timeout,
)


class ClaudeLLM:
    def __init__(self, settings, *, client: Any = None, circuit=None) -> None:
        if not settings.anthropic_api_key.get_secret_value():
            raise ProviderUnavailable("ANTHROPIC_API_KEY is required")
        if settings.anthropic_model != "claude-haiku-5-5":
            raise ProviderUnavailable("Select the explicitly approved and priced Haiku model")
        self.settings = settings
        self._client = (
            client.with_options(max_retries=0, timeout=settings.anthropic_attempt_timeout_secs)
            if client is not None and hasattr(client, "with_options")
            else client
        )
        self._owns_client = client is None
        self._closed = False
        self._prepare_slot = threading.BoundedSemaphore(1)
        self._late_client_closes: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self.circuit = circuit if circuit is not None else CircuitBreaker()
        self._last_failure: ProviderFailure | None = None
        self.tokens_used = self.spent_cents = 0

    @property
    def max_output_tokens(self) -> int:
        return self.settings.anthropic_max_output_tokens

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            return AsyncAnthropic(
                api_key=self.settings.anthropic_api_key.get_secret_value(),
                max_retries=0,
                timeout=self.settings.anthropic_attempt_timeout_secs,
            )
        return self._client

    async def aclose(self) -> None:
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None
        if self._late_client_closes:
            await asyncio.wait(tuple(self._late_client_closes),
                               timeout=min(1.0, self.settings.anthropic_attempt_timeout_secs))

    async def _prepare(self, request):
        """One bounded owner for cold imports/schema/client CPU work, never API I/O.

        Cancellation withdraws delivery, not worker ownership. A daemon worker
        retains admission until completion, so a late setup cannot overlap a
        successor or hold asyncio's executor shutdown open.
        """
        if self._closed:
            raise ProviderUnavailable("Claude adapter is closed")
        if not self._prepare_slot.acquire(blocking=False):
            raise ProviderFailure(FailureKind.UNAVAILABLE)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client_snapshot = self._client
        new_client = client_snapshot is None

        async def close_unused(client):
            try:
                async with asyncio.timeout(min(1.0, self.settings.anthropic_attempt_timeout_secs)):
                    await client.close()
            except Exception:  # noqa: BLE001 -- unused client never sent a request.
                pass

        def deliver(payload, client, error):
            try:
                if future.done() or self._closed:
                    if new_client and client is not None:
                        cleanup = asyncio.create_task(close_unused(client))
                        self._late_client_closes.add(cleanup)
                        cleanup.add_done_callback(self._late_client_closes.discard)
                    if not future.done():
                        future.set_exception(ProviderUnavailable("Claude adapter is closed"))
                    return
                if error is not None:
                    future.set_exception(error)
                    return
                if new_client:
                    self._client = client
                future.set_result(payload)
            finally:
                # Keep ownership through publication: a cancelled waiter can
                # release the request lock before this loop callback executes.
                self._prepare_slot.release()

        def work():
            payload = client = error = None
            try:
                payload = self._payload(request)
                # Shutdown may clear _client while payload work is running.
                # Reuse the admitted snapshot instead of constructing a new,
                # unowned replacement after the cached client was closed.
                client = self._get_client() if new_client else client_snapshot
            except ProviderUnavailable as exc:
                error = exc
            except Exception:  # noqa: BLE001 -- never expose constructor/schema payloads.
                error = ProviderUnavailable("Claude preparation failed")
            try:
                loop.call_soon_threadsafe(deliver, payload, client, error)
            except RuntimeError:
                # The caller loop closed. A newly constructed unused client has
                # opened no API connection and is never installed or reused.
                self._prepare_slot.release()

        try:
            threading.Thread(target=work, name="roma-claude-prepare", daemon=True).start()
        except RuntimeError:
            self._prepare_slot.release()
            raise ProviderUnavailable("Claude preparation could not start") from None
        async with asyncio.timeout(remaining_timeout(self.settings.anthropic_attempt_timeout_secs)):
            return await future

    def _reserve(self, input_bound, cap):
        # Conservatively price the ENTIRE bound as a cache write, never presume a hit.
        tokens = input_bound + cap
        cents = math.ceil(
            inr_for(
                Usage(input_bound, cap, cache_creation_input_tokens=input_bound),
                self.settings.anthropic_model,
            )
            * 100
        )
        if (
            self.tokens_used + tokens > self.settings.anthropic_call_token_budget
            or self.spent_cents + cents
            > math.floor(self.settings.anthropic_call_spend_cap_inr * 100)
        ):
            return None
        self.tokens_used += tokens
        self.spent_cents += cents
        return tokens, cents

    @staticmethod
    def _failure(exc):
        import httpx
        from anthropic import APIConnectionError, APIError, APITimeoutError

        if isinstance(exc, (TimeoutError, APITimeoutError, httpx.TimeoutException)):
            return ProviderFailure(FailureKind.TIMEOUT)
        if isinstance(exc, APIConnectionError):
            return ProviderFailure(FailureKind.TRANSIENT)
        if not isinstance(exc, APIError):
            return ProviderFailure(FailureKind.INVALID_REQUEST if isinstance(exc, ValueError)
                                   else FailureKind.UNAVAILABLE)
        body = exc.body if isinstance(exc.body, dict) else {}
        body = body.get("error", body)
        category = (str(body.get("type", "")) + " " + str(body.get("code", ""))) if isinstance(body, dict) else ""
        category = category.lower()
        status = getattr(exc, "status_code", None)
        if any(cue in category for cue in ("quota", "billing", "credit", "spend", "budget", "usage_limit")):
            kind = FailureKind.QUOTA
        elif status in {401, 403} or "authentication_error" in category:
            kind = FailureKind.AUTH
        elif status == 408 or "timeout_error" in category:
            kind = FailureKind.TIMEOUT
        elif status == 429 or "rate_limit_error" in category:
            kind = FailureKind.RATE_LIMIT
        elif status == 409 or (status is not None and status >= 500) or category.strip() in {"overloaded_error", "api_error"}:
            kind = FailureKind.TRANSIENT
        else:
            kind = FailureKind.INVALID_REQUEST
        return ProviderFailure(kind)

    @staticmethod
    def _retry_delay(exc, backoff):
        if not ClaudeLLM._failure(exc).retryable:
            return None
        response = getattr(exc, "response", None)
        value = response.headers.get("retry-after") if response is not None else None
        if value is not None:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    return None
            return max(0.0, delay) if math.isfinite(delay) else None
        return backoff

    def _payload(self, request):
        cap = (
            request.max_tokens
            if request.max_tokens is not None
            else self.settings.anthropic_max_output_tokens
        )
        if (
            (request.model and request.model != self.settings.anthropic_model)
            or not request.messages
            or len(request.messages) > 256
            or type(cap) is not int
            or not 1 <= cap <= self.settings.anthropic_max_output_tokens
        ):
            raise ProviderUnavailable("Invalid Claude model/request limits")
        system, messages = [], []
        size = 0
        for message in request.messages:
            if message.role not in {"system", "user", "assistant"} or not isinstance(
                message.content, str
            ):
                raise ProviderUnavailable("Invalid Claude message")
            value = redact_prompt(message.content)
            size += len(value.encode())
            if message.role == "system":
                system.append({"type": "text", "text": value})
            else:
                messages.append({"role": message.role, "content": value})
        if not messages or messages[-1]["role"] != "user":
            messages.append(
                {"role": "user", "content": "Respond only to the current controller task."}
            )
            size += 64
        if self.settings.anthropic_prompt_cache and system:
            # Mark stable rules only; do not pad a short prompt to force a cache hit.
            system[0]["cache_control"] = {"type": "ephemeral"}
        output_config = {"effort": self.settings.anthropic_effort}
        if request.response_schema is not None:
            schema = dict(request.response_schema)
            encoded = json.dumps(schema, allow_nan=False)
            if schema.get("type") != "object" or len(encoded.encode()) > 8192:
                raise ProviderUnavailable("Invalid bounded response schema")
            # The SDK removes unsupported numeric/string constraints from the wire
            # schema. Application Pydantic validation still enforces every constraint.
            from anthropic import transform_schema

            schema = transform_schema(schema)
            encoded = json.dumps(schema, allow_nan=False)
            if len(encoded.encode()) > 16384:
                raise ProviderUnavailable("Transformed response schema exceeds the bound")
            output_config["format"] = {"type": "json_schema", "schema": schema}
            size += len(encoded.encode())
        if size > self.settings.anthropic_max_prompt_bytes:
            raise ProviderUnavailable("Claude prompt byte budget exceeded")
        options = {
            "model": self.settings.anthropic_model,
            "messages": messages,
            "max_tokens": cap,
            "output_config": output_config,
            "thinking": {"type": "disabled"},
        }
        if system:
            options["system"] = system
        # No temperature/top_p/top_k or caller metadata is forwarded.
        return options, size + 128 + 64 * len(request.messages), cap

    def _usage(self, message):
        value = getattr(message, "usage", None)
        if value is None:
            return None
        counts = [
            getattr(value, "input_tokens", None),
            getattr(value, "output_tokens", None),
            getattr(value, "cache_read_input_tokens", 0) or 0,
            getattr(value, "cache_creation_input_tokens", 0) or 0,
        ]
        if (
            any(type(n) is not int or n < 0 for n in counts)
            or sum((counts[0], counts[2], counts[3])) <= 0
        ):
            return None
        return Usage(counts[0] + counts[2] + counts[3], counts[1], counts[2], counts[3])

    def _metrics(self, started, first, attempts, usage, charged, error):
        return LLMGenerationMetrics(
            model=self.settings.anthropic_model,
            ttft_ms=(first - started) * 1000 if first is not None else None,
            generation_secs=time.perf_counter() - started,
            attempts=attempts,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_input_tokens=usage.cached_input_tokens if usage else None,
            cost_inr=inr_for(usage, self.settings.anthropic_model) if usage else None,
            charged_inr=charged / 100,
            usage_known=usage is not None,
            call_tokens_used=self.tokens_used,
            call_spent_inr=self.spent_cents / 100,
            error_code=error,
            cache_creation_input_tokens=usage.cache_creation_input_tokens if usage else None,
            provider="claude",
        )

    async def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        started = time.perf_counter()
        deadline = time.monotonic() + remaining_timeout(self.settings.anthropic_turn_timeout_secs)
        permit = None
        acquired = False
        progress = {"first": None, "attempts": 0, "charged": 0}
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                await self._lock.acquire()
            acquired = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            permit = self.circuit.enter()
            async with aclosing(bounded_stream(self._generate_locked(request, started, progress), remaining)) as generation:
                async for chunk in generation:
                    if chunk.metrics is not None:
                        if chunk.metrics.error_code is None:
                            self.circuit.success(permit)
                        elif chunk.metrics.error_code != "budget_exhausted":
                            self.circuit.failure(permit, self._last_failure or ProviderFailure(FailureKind.UNAVAILABLE))
                        else:
                            self.circuit.abandon(permit)
                        permit = None
                    yield chunk
        except (TimeoutError, ProviderFailure) as exc:
            failure = exc if isinstance(exc, ProviderFailure) else ProviderFailure(FailureKind.TIMEOUT)
            if permit is not None:
                self.circuit.failure(permit, failure)
                permit = None
            error = "provider_timeout" if failure.kind == FailureKind.TIMEOUT else "provider_unavailable"
            yield LLMChunk(HARD_FAIL_LINE, error,
                           self._metrics(started, progress["first"], progress["attempts"], None,
                                         progress["charged"], error))
        finally:
            if permit is not None:
                self.circuit.abandon(permit)
            if acquired:
                self._lock.release()

    async def _attempt_await(self, operation, deadline):
        remaining = min(remaining_timeout(self.settings.anthropic_attempt_timeout_secs),
                        deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            return await operation()

    async def _generate_locked(self, request: LLMRequest, started, progress) -> AsyncGenerator[LLMChunk, None]:
        options, input_bound, cap = await self._prepare(request)
        first = None
        attempts = charged = 0
        error = "provider_unavailable"
        self._last_failure = None
        for index in range(self.settings.anthropic_max_retries + 1):
            if remaining_timeout(self.settings.anthropic_turn_timeout_secs) <= 0:
                raise TimeoutError
            reservation = self._reserve(input_bound, cap)
            if reservation is None:
                error = "budget_exhausted"
                break
            charged += reservation[1]
            attempts += 1
            progress.update(attempts=attempts, charged=charged)
            attempt_deadline = time.monotonic() + self.settings.anthropic_attempt_timeout_secs
            try:
                async with AsyncExitStack() as stack:
                    stream = await self._attempt_await(
                        lambda: stack.enter_async_context(self._client.messages.stream(**options)),
                        attempt_deadline,
                    )
                    texts = stream.text_stream.__aiter__()
                    while True:
                        try:
                            text = await self._attempt_await(texts.__anext__, attempt_deadline)
                        except StopAsyncIteration:
                            break
                        if text:
                            if first is None:
                                first = time.perf_counter()
                                progress["first"] = first
                            yield LLMChunk(text)
                    message = await self._attempt_await(stream.get_final_message, attempt_deadline)
                    usage = self._usage(message)
                    if (message.stop_reason not in {"end_turn", "max_tokens"}
                            or usage is None or (first is not None and usage.output_tokens <= 0)):
                        raise ValueError("Incomplete or unmetered Claude completion")
                    if any(block.type not in {"text", "thinking", "redacted_thinking"}
                           for block in message.content):
                        raise ValueError("Unexpected Claude tool response")
                    actual_cents = math.ceil(inr_for(usage, self.settings.anthropic_model) * 100)
                    self.tokens_used += usage.input_tokens + usage.output_tokens - reservation[0]
                    self.spent_cents += actual_cents - reservation[1]
                    charged += actual_cents - reservation[1]
                    progress["charged"] = charged
                    if first is None:
                        yield LLMChunk(HARD_FAIL_LINE, "provider_unavailable",
                                       self._metrics(started, first, attempts, usage, charged, "empty_output"))
                        return
                    yield LLMChunk("", "stop" if message.stop_reason == "end_turn" else "length",
                                   self._metrics(started, first, attempts, usage, charged, None))
                    return
            except Exception as exc:  # noqa: BLE001 -- strip SDK payloads/PII before exposing failure.
                self._last_failure = self._failure(exc)
                error = "provider_timeout" if self._last_failure.kind == FailureKind.TIMEOUT else "provider_unavailable"
                delay = self._retry_delay(exc, self.settings.anthropic_retry_backoff_secs * (2**index))
                remaining = remaining_timeout(self.settings.anthropic_turn_timeout_secs)
                if (first is not None or index >= self.settings.anthropic_max_retries
                        or delay is None or delay >= remaining):
                    break
                await asyncio.sleep(delay)
        yield LLMChunk(HARD_FAIL_LINE, error,
                       self._metrics(started, first, attempts, None, charged, error))
