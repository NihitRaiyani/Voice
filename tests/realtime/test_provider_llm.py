"""Offline protocol-to-Pipecat bridge and call-scoped accounting lifecycle."""

import asyncio

import pytest
from pipecat.frames.frames import (
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    MetricsFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import LLMSettings
from roma.core.ai_contracts import LLMChunk, LLMGenerationMetrics, LLMMessage, LLMRequest
from roma.domain.costs.spend import SpendLedger
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.realtime.provider_llm import CallLLMProvider, ProviderLLMService

METRICS = LLMGenerationMetrics(
    model="claude-haiku-5-5",
    ttft_ms=123,
    generation_secs=0.4,
    attempts=1,
    input_tokens=100,
    output_tokens=8,
    cached_input_tokens=20,
    cost_inr=0.01,
    charged_inr=0.01,
    usage_known=True,
    call_tokens_used=108,
    call_spent_inr=0.01,
    cache_creation_input_tokens=10,
)


class FakeProvider:
    max_output_tokens = 256

    def __init__(self):
        self.requests = []
        self.stream_closed = False
        self.client_closed = False
        self.spent_cents = 0

    async def generate(self, request):
        self.requests.append(request)
        try:
            self.spent_cents += 1
            yield LLMChunk("Namaste. ")
            yield LLMChunk("Kaise hain?")
            yield LLMChunk("", "end_turn", METRICS)
        finally:
            self.stream_closed = True

    async def aclose(self):
        self.client_closed = True


def context():
    return LLMContextFrame(
        LLMContext(
            messages=[
                {"role": "system", "content": "Speak safely"},
                {"role": "user", "content": "hello"},
            ]
        )
    )


def capture_frames(processor):
    frames = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        frames.append(frame)

    processor.push_frame = push
    return frames


def test_text_context_stream_and_stage_cap_cross_bridge():
    async def run():
        provider = FakeProvider()
        bridge = ProviderLLMService(provider, enable_direct_mode=True)
        frames = capture_frames(bridge)
        await bridge.process_frame(
            LLMUpdateSettingsFrame(delta=LLMSettings(max_tokens=80)), FrameDirection.DOWNSTREAM
        )
        await bridge.process_frame(context(), FrameDirection.DOWNSTREAM)
        assert provider.requests[0].max_tokens == 80
        assert provider.requests[0].messages[-1] == LLMMessage("user", "hello")
        assert isinstance(frames[0], LLMFullResponseStartFrame)
        assert isinstance(frames[-1], LLMFullResponseEndFrame)
        assert (
            "".join(frame.text for frame in frames if isinstance(frame, LLMTextFrame))
            == "Namaste. Kaise hain?"
        )
        metrics = next(frame for frame in frames if isinstance(frame, MetricsFrame))
        assert metrics.data[0].value == 0.123
        assert metrics.data[1].value.cache_creation_input_tokens == 10
        assert metrics.data[1].value.prompt_tokens == 100
        assert provider.stream_closed
        await bridge.cleanup()
        assert provider.client_closed

    asyncio.run(run())


def test_stage_cap_cannot_exceed_provider_limit():
    async def run():
        provider = FakeProvider()
        bridge = ProviderLLMService(provider, enable_direct_mode=True)
        capture_frames(bridge)
        await bridge.process_frame(
            LLMUpdateSettingsFrame(delta=LLMSettings(max_tokens=480)), FrameDirection.DOWNSTREAM
        )
        await bridge.process_frame(context(), FrameDirection.DOWNSTREAM)
        assert provider.requests[0].max_tokens == 256

    asyncio.run(run())


def test_unavailable_provider_gets_safe_text_and_completed_response():
    class Broken(FakeProvider):
        async def generate(self, request):
            raise RuntimeError("sensitive SDK response")
            yield

    async def run():
        bridge = ProviderLLMService(Broken(), enable_direct_mode=True)
        frames = capture_frames(bridge)
        await bridge.process_frame(context(), FrameDirection.DOWNSTREAM)
        assert [frame.text for frame in frames if isinstance(frame, LLMTextFrame)] == [
            HARD_FAIL_LINE
        ]
        assert isinstance(frames[-1], LLMFullResponseEndFrame)

    asyncio.run(run())


def test_cancel_closes_stream_without_flushing_half_sentence():
    async def run():
        waiting = asyncio.Event()

        class Slow(FakeProvider):
            async def generate(self, request):
                try:
                    yield LLMChunk("Half a sentence")
                    waiting.set()
                    await asyncio.Future()
                finally:
                    self.stream_closed = True

        provider = Slow()
        bridge = ProviderLLMService(provider, enable_direct_mode=True)
        frames = capture_frames(bridge)
        task = asyncio.create_task(bridge.process_frame(context(), FrameDirection.DOWNSTREAM))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.stream_closed
        assert not any(isinstance(frame, LLMFullResponseEndFrame) for frame in frames)
        await bridge.cleanup()
        assert provider.client_closed

    asyncio.run(run())


def test_wording_and_extraction_both_charge_the_same_call_ledger(tmp_path):
    async def run():
        ledger = SpendLedger(tmp_path / "spend.jsonl")
        provider = CallLLMProvider(FakeProvider(), ledger=ledger, call_sid="CA_synthetic")
        for purpose in ("extraction", "wording"):
            async for _ in provider.generate(
                LLMRequest((LLMMessage("user", "fixture"),), metadata={"purpose": purpose})
            ):
                pass
        assert provider.spent_inr == pytest.approx(0.02)
        assert ledger.spent_inr == pytest.approx(0.02)
        assert len(ledger.path.read_text().splitlines()) == 2

    asyncio.run(run())


def test_cancelled_attempt_still_persists_its_reserved_charge(tmp_path):
    async def run():
        ledger = SpendLedger(tmp_path / "spend.jsonl")
        provider = CallLLMProvider(FakeProvider(), ledger=ledger)
        stream = provider.generate(LLMRequest((LLMMessage("user", "fixture"),)))
        await anext(stream)
        await stream.aclose()
        assert provider.spent_inr == ledger.spent_inr == 0.01
        assert provider.provider.stream_closed

    asyncio.run(run())


def test_accounting_failure_prevents_the_next_provider_call():
    class BrokenLedger:
        def record_charge(self, *args, **kwargs):
            raise OSError("disk unavailable")

    async def run():
        fake = FakeProvider()
        provider = CallLLMProvider(fake, ledger=BrokenLedger())
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                async for _ in provider.generate(LLMRequest((LLMMessage("user", "fixture"),))):
                    pass
        assert len(fake.requests) == 1

    asyncio.run(run())


def test_streamed_provider_text_still_crosses_sentence_safety_before_tts():
    from pipecat.frames.frames import TextFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.tests.utils import run_test
    from roma.domain.safety import safe_output
    from roma.realtime.pretts import PreTTSFilterProcessor
    from roma.realtime.sentences import InterruptibleSentenceAggregator

    class UnsafeProvider(FakeProvider):
        async def generate(self, request):
            yield LLMChunk("Fees pachaas ")
            yield LLMChunk("hazaar hai.")
            yield LLMChunk("", "stop", METRICS)

    bridge = ProviderLLMService(UnsafeProvider())
    processor = Pipeline([bridge, InterruptibleSentenceAggregator(), PreTTSFilterProcessor()])
    down, _ = asyncio.run(run_test(processor, frames_to_send=[context()]))
    spoken = "".join(frame.text for frame in down if isinstance(frame, TextFrame))
    assert spoken.strip() == safe_output("Fees pachaas hazaar hai.")
    assert "pachaas" not in spoken


def test_prefix_diagnostic_failure_cannot_drop_a_charge(tmp_path):
    async def run():
        ledger = SpendLedger(tmp_path / "spend.jsonl")
        provider = CallLLMProvider(FakeProvider(), ledger=ledger)

        def broken_prefix():
            raise RuntimeError("diagnostic unavailable")

        provider.prefix_fn = broken_prefix
        async for _ in provider.generate(LLMRequest((LLMMessage("user", "fixture"),))):
            pass
        assert ledger.spent_inr == 0.01

    asyncio.run(run())


def test_accounting_failure_during_cancel_preserves_cancel_and_clears_stream():
    async def run():
        started = asyncio.Event()

        class Slow(FakeProvider):
            async def generate(self, request):
                try:
                    self.spent_cents += 1
                    yield LLMChunk("Partial sentence")
                    started.set()
                    await asyncio.Future()
                finally:
                    self.stream_closed = True

        class BrokenLedger:
            def record_charge(self, *args, **kwargs):
                raise OSError("disk unavailable")

        provider = CallLLMProvider(Slow(), ledger=BrokenLedger())
        bridge = ProviderLLMService(provider, enable_direct_mode=True)
        frames = capture_frames(bridge)
        task = asyncio.create_task(bridge.process_frame(context(), FrameDirection.DOWNSTREAM))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.accounting_failed
        assert provider.provider.stream_closed
        assert not any(isinstance(frame, LLMFullResponseEndFrame) for frame in frames)
        assert HARD_FAIL_LINE not in [
            frame.text for frame in frames if isinstance(frame, LLMTextFrame)
        ]

    asyncio.run(run())
