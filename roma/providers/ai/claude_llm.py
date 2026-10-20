"""Bounded Anthropic async streaming behind the transport-neutral LLMProvider.

Only text deltas cross this adapter. Stage, facts, slots and final speech safety
remain application-owned. Automatic SDK retries are disabled to bound one turn.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator
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


class ClaudeLLM:
    def __init__(self, settings, *, client: Any = None) -> None:
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
        self._lock = asyncio.Lock()
        self.tokens_used = self.spent_cents = 0

    @property
    def max_output_tokens(self) -> int:
        return self.settings.anthropic_max_output_tokens

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(
                api_key=self.settings.anthropic_api_key.get_secret_value(),
                max_retries=0,
                timeout=self.settings.anthropic_attempt_timeout_secs,
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None

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
    def _retry_delay(exc, backoff):
        from anthropic import APIConnectionError, APIStatusError

        if isinstance(exc, (TimeoutError, APIConnectionError)):
            return backoff
        if not isinstance(exc, APIStatusError):
            return None
        body = exc.body if isinstance(exc.body, dict) else {}
        body = body.get("error", body)
        category = (
            str(body.get("type", "")) + " " + str(body.get("code", ""))
            if isinstance(body, dict)
            else ""
        )
        if any(
            cue in category.lower()
            for cue in ("quota", "billing", "credit", "spend", "budget", "usage_limit")
        ):
            return None
        if exc.status_code not in {408, 409, 429} and exc.status_code < 500:
            return None
        value = exc.response.headers.get("retry-after")
        if value is not None:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    return None
            # A malformed Retry-After is not permission to hammer a provider.
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
        options, input_bound, cap = self._payload(request)
        async with self._lock:
            started = time.perf_counter()
            first = None
            attempts = charged = 0
            error = "provider_unavailable"
            try:
                async with asyncio.timeout(self.settings.anthropic_turn_timeout_secs):
                    for index in range(self.settings.anthropic_max_retries + 1):
                        reservation = self._reserve(input_bound, cap)
                        if reservation is None:
                            error = "budget_exhausted"
                            break
                        charged += reservation[1]
                        attempts += 1
                        try:
                            async with asyncio.timeout(
                                self.settings.anthropic_attempt_timeout_secs
                            ):
                                async with self._get_client().messages.stream(
                                    **options
                                ) as stream:
                                    async for text in stream.text_stream:
                                        if text:
                                            if first is None:
                                                first = time.perf_counter()
                                            yield LLMChunk(text)
                                    message = await stream.get_final_message()
                                    usage = self._usage(message)
                                    if (
                                        message.stop_reason not in {"end_turn", "max_tokens"}
                                        or usage is None
                                        or (first is not None and usage.output_tokens <= 0)
                                    ):
                                        raise ValueError(
                                            "Incomplete or unmetered Claude completion"
                                        )
                                    if any(
                                        block.type
                                        not in {"text", "thinking", "redacted_thinking"}
                                        for block in message.content
                                    ):
                                        raise ValueError("Unexpected Claude tool response")
                                    actual_cents = math.ceil(
                                        inr_for(usage, self.settings.anthropic_model) * 100
                                    )
                                    # If a provider violates our bound, retain its larger actual usage.
                                    self.tokens_used += (
                                        usage.input_tokens
                                        + usage.output_tokens
                                        - reservation[0]
                                    )
                                    self.spent_cents += actual_cents - reservation[1]
                                    charged += actual_cents - reservation[1]
                                    if first is None:
                                        yield LLMChunk(
                                            HARD_FAIL_LINE,
                                            "provider_unavailable",
                                            self._metrics(
                                                started,
                                                first,
                                                attempts,
                                                usage,
                                                charged,
                                                "empty_output",
                                            ),
                                        )
                                        return
                                    yield LLMChunk(
                                        "",
                                        "stop"
                                        if message.stop_reason == "end_turn"
                                        else "length",
                                        self._metrics(
                                            started, first, attempts, usage, charged, None
                                        ),
                                    )
                                    return
                        except Exception as exc:  # noqa: BLE001 -- strip SDK payloads/PII before exposing failure.
                            delay = self._retry_delay(
                                exc, self.settings.anthropic_retry_backoff_secs * (2**index)
                            )
                            remaining = self.settings.anthropic_turn_timeout_secs - (
                                time.perf_counter() - started
                            )
                            if (
                                first is not None
                                or index >= self.settings.anthropic_max_retries
                                or delay is None
                                or delay >= remaining
                            ):
                                break
                            await asyncio.sleep(delay)
            except TimeoutError:
                error = "provider_timeout"
            yield LLMChunk(
                HARD_FAIL_LINE,
                error,
                self._metrics(started, first, attempts, None, charged, error),
            )
