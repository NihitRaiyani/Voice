"""Thin streaming SDK adapter. One instance/budget per text call; no audio or stage authority."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator
from typing import Any

from roma.core.prompt_privacy import redact_prompt
from roma.domain.costs.spend import PRICES, Usage, inr_for
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.ai.contracts import (
    LLMChunk,
    LLMGenerationMetrics,
    LLMRequest,
    ProviderUnavailable,
)


class OpenAILLM:
    def __init__(self, settings, *, client: Any = None) -> None:
        if not settings.openai_model or not settings.openai_api_key.get_secret_value():
            raise ProviderUnavailable("OPENAI_MODEL and OPENAI_API_KEY are required")
        if settings.openai_model not in PRICES:
            raise ProviderUnavailable(
                "Configure an explicitly priced supported model before use"
            )
        self.settings = settings
        self._client = (
            client.with_options(max_retries=0, timeout=settings.openai_attempt_timeout_secs)
            if client is not None and hasattr(client, "with_options")
            else client
        )
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self.tokens_used = 0
        self.spent_cents = 0

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
                max_retries=0,
                timeout=self.settings.openai_attempt_timeout_secs,
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        from openai import APIConnectionError, APIStatusError

        if isinstance(exc, (TimeoutError, APIConnectionError)):
            return True
        if not isinstance(exc, APIStatusError):
            return False
        body = exc.body if isinstance(exc.body, dict) else {}
        body = body.get("error", body)
        if not isinstance(body, dict):
            body = {}
        category = str(body.get("code", "")) + " " + str(body.get("type", ""))
        if any(
            cue in category.lower()
            for cue in ("quota", "billing", "credit", "spend", "usage_limit", "budget")
        ):
            return False
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500

    def _reserve(self, input_bound: int, output_cap: int) -> tuple[int, int] | None:
        tokens = input_bound + output_cap
        cents = math.ceil(
            inr_for(Usage(input_bound, output_cap), self.settings.openai_model) * 100
        )
        if (
            self.tokens_used + tokens > self.settings.openai_call_token_budget
            or self.spent_cents + cents
            > math.floor(self.settings.openai_call_spend_cap_inr * 100)
        ):
            return None
        self.tokens_used += tokens
        self.spent_cents += cents
        return tokens, cents

    def _settle(self, reservation: tuple[int, int], usage: Usage | None) -> None:
        if usage is None:
            return
        tokens = usage.input_tokens + usage.output_tokens
        cents = math.ceil(inr_for(usage, self.settings.openai_model) * 100)
        self.tokens_used += tokens - reservation[0]
        self.spent_cents += cents - reservation[1]

    def _metrics(
        self,
        started: float,
        first: float | None,
        attempts: int,
        usage: Usage | None,
        charged_cents: int,
        error: str | None,
    ):
        return LLMGenerationMetrics(
            model=self.settings.openai_model,
            ttft_ms=(first - started) * 1000 if first is not None else None,
            generation_secs=time.perf_counter() - started,
            attempts=attempts,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_input_tokens=usage.cached_input_tokens if usage else None,
            cost_inr=inr_for(usage, self.settings.openai_model) if usage else None,
            charged_inr=charged_cents / 100,
            usage_known=usage is not None,
            call_tokens_used=self.tokens_used,
            call_spent_inr=self.spent_cents / 100,
            error_code=error,
        )

    async def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        if request.model and request.model != self.settings.openai_model:
            raise ProviderUnavailable("Request model cannot bypass the configured/priced model")
        cap = (
            request.max_tokens
            if request.max_tokens is not None
            else self.settings.openai_max_output_tokens
        )
        if (
            not request.messages
            or type(cap) is not int
            or not 1 <= cap <= self.settings.openai_max_output_tokens
            or not math.isfinite(request.temperature)
            or not 0 <= request.temperature <= 2
        ):
            raise ProviderUnavailable("Invalid generation limits")
        messages = []
        for m in request.messages:
            if m.role not in {"system", "user", "assistant"} or not isinstance(m.content, str):
                raise ProviderUnavailable("Invalid messages")
            messages.append({"role": m.role, "content": redact_prompt(m.content)})
        byte_count = sum(len(m["content"].encode("utf-8")) for m in messages)
        if byte_count > self.settings.openai_max_prompt_bytes:
            raise ProviderUnavailable("Prompt byte budget exceeded")
        # Conservative byte-based admission, NOT a tokenizer measurement. Include generous role/template overhead.
        input_bound = byte_count + 64 + 32 * len(messages)
        async with self._lock:
            started = time.perf_counter()
            first = None
            attempts = 0
            charged = 0
            usage = None
            error = "provider_unavailable"
            try:
                async with asyncio.timeout(self.settings.openai_turn_timeout_secs):
                    for index in range(self.settings.openai_max_retries + 1):
                        reservation = self._reserve(input_bound, cap)
                        if reservation is None:
                            error = "budget_exhausted"
                            break
                        charged += reservation[1]
                        attempts += 1
                        stream = None
                        usage = None
                        try:
                            async with asyncio.timeout(
                                self.settings.openai_attempt_timeout_secs
                            ):
                                options: dict[str, Any] = {}
                                if request.metadata.get("purpose") == "extraction":
                                    options["response_format"] = {"type": "json_object"}
                                stream = await self._get_client().chat.completions.create(
                                    model=self.settings.openai_model,
                                    messages=messages,
                                    stream=True,
                                    stream_options={"include_usage": True},
                                    store=False,
                                    max_completion_tokens=cap,
                                    temperature=request.temperature,
                                    **options,
                                )
                                finish = None
                                async for event in stream:
                                    if event.usage is not None:
                                        counts = (
                                            event.usage.prompt_tokens,
                                            event.usage.completion_tokens,
                                        )
                                        if (
                                            any(type(n) is not int for n in counts)
                                            or counts[0] <= 0
                                        ):
                                            raise ValueError("Missing or invalid token counts")
                                        if first is not None and counts[1] <= 0:
                                            raise ValueError(
                                                "Text completion lacks output usage"
                                            )
                                        reported = Usage.from_response(event)
                                        if (
                                            reported.input_tokens < 0
                                            or reported.output_tokens < 0
                                            or not 0
                                            <= reported.cached_input_tokens
                                            <= reported.input_tokens
                                        ):
                                            raise ValueError("Invalid token usage")
                                        usage = reported
                                    for choice in event.choices:
                                        if choice.delta.content:
                                            if first is None:
                                                first = time.perf_counter()
                                            yield LLMChunk(choice.delta.content)
                                        if choice.finish_reason:
                                            finish = choice.finish_reason
                                if finish not in {"stop", "length"}:
                                    raise ValueError("Incomplete or unsupported completion")
                            self._settle(reservation, usage)
                            if usage is not None:
                                charged += (
                                    math.ceil(inr_for(usage, self.settings.openai_model) * 100)
                                    - reservation[1]
                                )
                            yield LLMChunk(
                                "",
                                finish,
                                self._metrics(started, first, attempts, usage, charged, None),
                            )
                            return
                        except Exception as exc:  # noqa: BLE001 -- SDK/transport/payload errors are untrusted; never log their text.
                            if (
                                first is not None
                                or index >= self.settings.openai_max_retries
                                or not self._retryable(exc)
                            ):
                                break
                            await asyncio.sleep(
                                self.settings.openai_retry_backoff_secs * (2**index)
                            )
                        finally:
                            if stream is not None:
                                try:
                                    async with asyncio.timeout(0.5):
                                        await stream.close()
                                except Exception:  # noqa: BLE001 -- bounded best-effort transport cleanup; preserve cancellation.
                                    pass
            except TimeoutError:
                error = "provider_timeout"
            yield LLMChunk(
                HARD_FAIL_LINE,
                error,
                self._metrics(started, first, attempts, None, charged, error),
            )
