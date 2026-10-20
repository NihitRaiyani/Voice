"""Pipecat text-frame bridge over the bounded provider protocol."""

from __future__ import annotations

import asyncio
from contextlib import aclosing

from pipecat.frames.frames import (
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    MetricsFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData, TTFBMetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.ai.contracts import LLMMessage, LLMRequest


class CallLLMProvider:
    """One call's shared extraction/wording budget and persistent conservative charges.

    The provider's own reservation counters also include attempts cancelled before usage
    arrives. Persist their delta, rather than dropping those charges at interruption.
    """

    def __init__(self, provider, *, ledger=None, call_sid=None):
        self.provider = provider
        self.ledger = ledger
        self.call_sid = call_sid
        self.spent_inr = 0.0
        self.prefix_fn = None
        self._lock = asyncio.Lock()
        self.accounting_failed = False

    @property
    def max_output_tokens(self):
        settings = getattr(self.provider, "settings", None)
        return getattr(
            self.provider,
            "max_output_tokens",
            getattr(settings, "anthropic_max_output_tokens", 512),
        )

    async def generate(self, request):
        async with self._lock:
            if self.accounting_failed:
                raise RuntimeError("Call accounting unavailable")
            before = getattr(self.provider, "spent_cents", 0)
            final = None
            interrupted = False
            try:
                async with aclosing(self.provider.generate(request)) as stream:
                    async for chunk in stream:
                        if chunk.metrics is not None:
                            final = chunk.metrics
                        yield chunk
            except (asyncio.CancelledError, GeneratorExit):
                interrupted = True
                raise
            finally:
                charged = (getattr(self.provider, "spent_cents", before) - before) / 100
                if final is not None:
                    charged = final.charged_inr
                self.spent_inr += charged
                if self.ledger is not None and charged > 0:
                    try:
                        prefix = self.prefix_fn() if self.prefix_fn is not None else None
                    except Exception:  # noqa: BLE001 -- a diagnostic cannot drop a charge.
                        prefix = None
                    settings = getattr(self.provider, "settings", None)
                    model = (
                        final.model
                        if final is not None
                        else getattr(settings, "anthropic_model", "claude-haiku-5-5")
                    )
                    try:
                        self.ledger.record_charge(
                            charged, model, call_sid=self.call_sid, prefix=prefix
                        )
                    except Exception:
                        self.accounting_failed = True
                        if not interrupted:
                            raise

    async def aclose(self):
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            await close()


class ProviderLLMService(FrameProcessor):
    """Translate only text context/settings/results; downstream safety still owns speech."""

    def __init__(self, provider, *, max_tokens=256, owns_provider=True, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self.max_tokens = max_tokens
        self.owns_provider = owns_provider

    async def _metrics_frames(self, metrics):
        data = []
        if metrics.ttft_ms is not None:
            data.append(
                TTFBMetricsData(
                    processor=self.name, model=metrics.model, value=metrics.ttft_ms / 1000
                )
            )
        if metrics.usage_known:
            data.append(
                LLMUsageMetricsData(
                    processor=self.name,
                    model=metrics.model,
                    value=LLMTokenUsage(
                        prompt_tokens=metrics.input_tokens or 0,
                        completion_tokens=metrics.output_tokens or 0,
                        total_tokens=(metrics.input_tokens or 0) + (metrics.output_tokens or 0),
                        cache_read_input_tokens=metrics.cached_input_tokens or 0,
                        cache_creation_input_tokens=metrics.cache_creation_input_tokens,
                    ),
                )
            )
        if data:
            await self.push_frame(MetricsFrame(data=data))

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMUpdateSettingsFrame):
            if frame.service is not None and frame.service is not self:
                await self.push_frame(frame, direction)
                return
            cap = (
                getattr(frame.delta, "max_tokens", None)
                if frame.delta is not None
                else frame.settings.get("max_tokens")
            )
            # Settings sent by StageController are integral stage word caps. All limits
            # are validated again by the provider before reserving tokens or making I/O.
            if type(cap) is int:
                self.max_tokens = cap
            return
        if not isinstance(frame, LLMContextFrame) or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        cancelled = False
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            messages = []
            for message in frame.context.get_messages():
                if not isinstance(message, dict):
                    raise ValueError("Unsupported non-text model context")
                role, content = message.get("role"), message.get("content")
                if not isinstance(content, str):
                    raise ValueError("Unsupported non-text model content")
                if role == "system":
                    messages.append(LLMMessage("system", content))
                elif role == "user":
                    messages.append(LLMMessage("user", content))
                elif role == "assistant":
                    messages.append(LLMMessage("assistant", content))
                else:
                    raise ValueError("Unsupported model context role")
            async with aclosing(
                self.provider.generate(
                    LLMRequest(
                        messages=tuple(messages),
                        max_tokens=min(
                            self.max_tokens,
                            getattr(self.provider, "max_output_tokens", self.max_tokens),
                        ),
                    )
                )
            ) as stream:
                async for chunk in stream:
                    if chunk.text:
                        await self.push_frame(LLMTextFrame(chunk.text))
                    if chunk.metrics is not None:
                        await self._metrics_frames(chunk.metrics)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:  # noqa: BLE001 -- fail closed without logging caller/provider text.
            await self.push_frame(LLMTextFrame(HARD_FAIL_LINE))
        finally:
            # An end frame flushes buffered sentences. On interruption Pipecat clears
            # those buffers; never flush a half-generated sentence during teardown.
            if not cancelled:
                await self.push_frame(LLMFullResponseEndFrame())

    async def cleanup(self):
        await super().cleanup()
        if self.owns_provider:
            close = getattr(self.provider, "aclose", None)
            if close is not None:
                await close()
