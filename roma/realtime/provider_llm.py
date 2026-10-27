"""Pipecat text-frame bridge over the bounded provider protocol."""

from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from dataclasses import replace
from uuid import uuid4

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

from roma.core.telemetry import get_telemetry
from roma.core.turn_epoch import current_turn_epoch, require_current_epoch
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.ai.contracts import LLMChunk, LLMMessage, LLMRequest
from roma.providers.reliability import FailureKind, ProviderFailure, bounded_stream


def _require_epoch(guard, epoch):
    require_current_epoch()
    scoped = current_turn_epoch()
    if scoped is not None and epoch is not None and scoped != epoch:
        raise asyncio.CancelledError('Stale turn')
    if guard is not None:
        guard.require_current(epoch)


def _generation_outcome(provider, chunk):
    code = chunk.metrics.error_code if chunk.metrics is not None else chunk.finish_reason
    if code is None or (chunk.metrics is None and code not in {
            'provider_timeout', 'provider_unavailable', 'budget_exhausted'}):
        return 'ok'
    if code == 'provider_timeout':
        return 'timeout'
    if code == 'budget_exhausted':
        return 'rejected'
    failure = getattr(provider, '_last_failure', None)
    if (code == 'provider_unavailable' and chunk.metrics is not None
            and chunk.metrics.attempts > 0 and isinstance(failure, ProviderFailure)
            and failure.kind == FailureKind.RATE_LIMIT):
        return 'rate_limit'
    return 'error'


class CallLLMProvider:
    """One call's shared extraction/wording budget and persistent conservative charges.

    The provider's own reservation counters also include attempts cancelled before usage
    arrives. Persist their delta, rather than dropping those charges at interruption.
    """

    def __init__(self, provider, *, ledger=None, call_sid=None, usage_sink=None,
                 usage_turn_snapshot=None, epoch=None) -> None:
        self.provider = provider
        self.ledger = ledger
        self.call_sid = call_sid
        self.usage_sink = usage_sink
        self.usage_turn_snapshot, self.epoch = usage_turn_snapshot, epoch
        self.spent_inr = 0.0
        self.prefix_fn = None
        self._lock = asyncio.Lock()
        self.accounting_failed = False
        self._turn_deadline: float | None = None

    @property
    def max_output_tokens(self):
        settings = getattr(self.provider, "settings", None)
        return getattr(
            self.provider,
            "max_output_tokens",
            getattr(settings, "anthropic_max_output_tokens", 512),
        )

    def begin_turn(self):
        """A new informative caller context owns one extraction/wording deadline."""
        settings = getattr(self.provider, "settings", None)
        duration = getattr(settings, "anthropic_turn_timeout_secs", None)
        self._turn_deadline = time.monotonic() + duration if duration is not None else None

    async def generate(self, request):
        telemetry, outcome, completed = get_telemetry(), 'ok', False
        started = telemetry.clock()
        try:
            epoch = request.turn_epoch
            if epoch is None:
                epoch = current_turn_epoch()
            if epoch is None and self.epoch is not None:
                epoch = self.epoch.snapshot()
            _require_epoch(self.epoch, epoch)
            request = replace(request, turn_epoch=epoch)
            async with aclosing(self._deadline_generate(request)) as stream:
                async for chunk in stream:
                    if chunk.metrics is not None or chunk.finish_reason in {
                            'provider_timeout', 'provider_unavailable', 'budget_exhausted'}:
                        outcome = _generation_outcome(self.provider, chunk)
                        completed = chunk.metrics is not None or chunk.finish_reason is not None
                    _require_epoch(self.epoch, epoch)
                    yield chunk
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except GeneratorExit:
            if not completed:
                outcome = 'cancelled'
            raise
        except TimeoutError:
            outcome = 'timeout'
            raise
        except ProviderFailure as exc:
            outcome = ('rate_limit' if exc.kind == FailureKind.RATE_LIMIT else
                       'timeout' if exc.kind == FailureKind.TIMEOUT else 'error')
            raise
        except BaseException:
            outcome = 'error'
            raise
        finally:
            telemetry.observe_latency('provider', max(0, telemetry.clock() - started), outcome=outcome)
            telemetry.event('claude', outcome=outcome)

    async def _deadline_generate(self, request):
        deadline = self._turn_deadline
        if deadline is None:
            async with aclosing(self._generate(request)) as stream:
                async for chunk in stream:
                    yield chunk
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _require_epoch(self.epoch, request.turn_epoch)
            yield LLMChunk(HARD_FAIL_LINE, "provider_timeout")
            return
        try:
            async with aclosing(bounded_stream(self._generate(request), remaining)) as stream:
                async for chunk in stream:
                    yield chunk
        except TimeoutError:
            _require_epoch(self.epoch, request.turn_epoch)
            yield LLMChunk(HARD_FAIL_LINE, "provider_timeout")

    async def _generate(self, request):
        # Capture attribution before admission can yield to another turn.
        usage_turn = self.usage_turn_snapshot() if self.usage_turn_snapshot is not None else None
        _require_epoch(self.epoch, request.turn_epoch)
        async with self._lock:
            _require_epoch(self.epoch, request.turn_epoch)
            if self.accounting_failed:
                raise RuntimeError("Call accounting unavailable")
            before = getattr(self.provider, "spent_cents", 0)
            final = None
            usage_request_id = str(uuid4())
            interrupted = False
            try:
                async with aclosing(self.provider.generate(request)) as stream:
                    async for chunk in stream:
                        if chunk.metrics is not None:
                            # Late usage is still a durable cost fact even when its
                            # associated text is no longer allowed to escape.
                            final = chunk.metrics
                        _require_epoch(self.epoch, request.turn_epoch)
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

                if self.usage_sink is not None:
                    try:
                        if self.usage_turn_snapshot is None:
                            await self.usage_sink(final, usage_request_id)
                        else:
                            await self.usage_sink(final, usage_request_id, turn=usage_turn)
                    except Exception:  # noqa: BLE001 — unknown durable usage blocks further paid work.
                        self.accounting_failed = True
                        if not interrupted:
                            raise RuntimeError("Durable usage accounting unavailable") from None

    async def aclose(self):
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            await close()


class ProviderLLMService(FrameProcessor):
    """Translate only text context/settings/results; downstream safety still owns speech."""

    def __init__(self, provider, *, max_tokens=256, owns_provider=True, output_policy=None,
                 epoch=None, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self.max_tokens = max_tokens
        self.owns_provider = owns_provider
        self.output_policy = output_policy
        self.epoch = epoch

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
        guard = self.epoch or getattr(self.provider, 'epoch', None)
        epoch = current_turn_epoch()
        if epoch is None and guard is not None:
            epoch = guard.snapshot()
        _require_epoch(guard, epoch)
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
            if self.output_policy == 'voice_hi':
                from roma.services.conversation_prompts import LANGUAGE_INSTRUCTIONS
                messages.append(LLMMessage('system', LANGUAGE_INSTRUCTIONS['voice_hi']))
            async with aclosing(
                self.provider.generate(
                    LLMRequest(
                        messages=tuple(messages),
                        turn_epoch=epoch,
                        max_tokens=min(
                            self.max_tokens,
                            getattr(self.provider, "max_output_tokens", self.max_tokens),
                        ),
                    )
                )
            ) as stream:
                async for chunk in stream:
                    _require_epoch(guard, epoch)
                    if chunk.text:
                        await self.push_frame(LLMTextFrame(chunk.text))
                    if chunk.metrics is not None:
                        await self._metrics_frames(chunk.metrics)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:  # noqa: BLE001 -- fail closed without logging caller/provider text.
            _require_epoch(guard, epoch)
            await self.push_frame(LLMTextFrame(HARD_FAIL_LINE))
        finally:
            # An end frame flushes buffered sentences. On interruption Pipecat clears
            # those buffers; never flush a half-generated sentence during teardown.
            if not cancelled:
                _require_epoch(guard, epoch)
                await self.push_frame(LLMFullResponseEndFrame())

    async def cleanup(self):
        await super().cleanup()
        if self.owns_provider:
            close = getattr(self.provider, "aclose", None)
            if close is not None:
                await close()
