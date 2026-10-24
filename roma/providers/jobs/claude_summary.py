"""Opt-in, metadata-only Claude summaries with a separate per-attempt budget.

One SQL job permits at most five attempts: each uses at most 2,048 reserved tokens
and INR 0.25. This is ordinary Messages streaming, not the Message Batches API.
"""

import asyncio
import json
import math

from roma.core.ai_contracts import LLMMessage, LLMRequest, ProviderUnavailable
from roma.core.prompt_privacy import redact_prompt
from roma.providers.ai.claude_llm import ClaudeLLM

_STAGES = {"open", "discover", "value", "structure", "pivot", "objection", "close", "unknown"}
_OUTCOMES = {"locked", "no_lock", "unknown"}


class ClaudeSummary:
    def __init__(self, settings, *, allow_paid_api=False, client=None) -> None:
        if allow_paid_api is not True:
            raise ProviderUnavailable("Claude summaries require explicit paid API opt-in")
        self.settings = settings.model_copy(
            update={
                "anthropic_max_output_tokens": 128,
                "anthropic_max_prompt_bytes": 2048,
                "anthropic_attempt_timeout_secs": 10,
                "anthropic_turn_timeout_secs": 12,
                "anthropic_max_retries": 0,
                "anthropic_call_token_budget": 2048,
                "anthropic_call_spend_cap_inr": 0.25,
                "anthropic_prompt_cache": False,
            }
        )
        self._client = client
        self._lock = asyncio.Lock()

    async def summarize(self, facts):
        stage = facts.get("final_stage") or "unknown"
        outcome = facts.get("outcome", "unknown")
        if stage not in _STAGES or outcome not in _OUTCOMES:
            raise ValueError("Invalid summary metadata")
        metadata = {"final_stage": stage, "outcome": outcome}
        for key in ("duration_secs", "audio_secs", "turn_count"):
            value = facts.get(key, 0)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 86400
            ):
                raise ValueError("Invalid summary metadata")
            metadata[key] = value
        request = LLMRequest(
            (
                LLMMessage(
                    "system",
                    "Summarize these call metadata in one short factual sentence. Do not invent caller details, promises, appointment confirmation, or followup delivery. A captured visit time is not a committed booking.",
                ),
                LLMMessage("user", json.dumps(metadata, allow_nan=False)),
            ),
            max_tokens=128,
        )
        # Dedicated background instances never share the live provider or its budget.
        async with self._lock, asyncio.timeout(15):
            provider = ClaudeLLM(self.settings, client=self._client)
            text = ""
            terminal = None
            try:
                async for chunk in provider.generate(request):
                    text += chunk.text
                    if len(text.encode()) > 2048:
                        raise ProviderUnavailable("Summary output exceeds the bound")
                    if chunk.finish_reason is not None:
                        terminal = chunk
                if terminal is None or terminal.finish_reason != "stop" or not text.strip():
                    raise ProviderUnavailable("Summary is incomplete")
                metrics = terminal.metrics
                if metrics is None or not metrics.usage_known or metrics.error_code:
                    raise ProviderUnavailable("Summary usage is unavailable")
                return {
                    "text": redact_prompt(text.strip()),
                    "source": "claude_call_metadata",
                    "input_tokens": metrics.input_tokens,
                    "output_tokens": metrics.output_tokens,
                    "charged_inr": metrics.charged_inr,
                }
            finally:
                await provider.aclose()
