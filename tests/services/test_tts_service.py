import asyncio
from dataclasses import replace

import pytest
from roma.domain.safety.audit import SafetyContext
from roma.providers.ai.contracts import AudioFormat, TTSRequest, TTSResult
from roma.services.safety_service import InMemorySafetyEventStore, SafetyService
from roma.services.tts_service import SpeechSynthesisService, normalize_tts_text

CTX = SafetyContext("synthetic-tts", 1, "lab")


class PCMProvider:
    def __init__(self, audio=b"\0\0" * 160, wait=False):
        self.calls = []
        self.audio = audio
        self.wait = wait
        self.cancelled = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(self, request):
        self.calls.append(request)
        self.started.set()
        try:
            if self.wait:
                await self.release.wait()
            return TTSResult(self.audio, request.format)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def service(provider=None, safety=None, **kwargs):
    return SpeechSynthesisService(
        provider or PCMProvider(), safety or SafetyService(), model_version="test-v1", **kwargs
    )


@pytest.mark.parametrize("language,word", [("hi", "पाँच"), ("gu", "પાંચ"), ("en", "five")])
def test_normalization_preserves_combining_marks_and_expands_dates_terms_digits(language, word):
    result = normalize_tts_text("  SEO Digital Marketing 2026-11-05 ५૫5 ", language)
    assert word in result
    assert "2026" not in result and "11-05" not in result and "SEO" not in result
    assert result == normalize_tts_text(result, language)


def test_invalid_date_control_and_language_rejected():
    with pytest.raises(ValueError):
        normalize_tts_text("2026-02-30", "hi")
    with pytest.raises(ValueError):
        normalize_tts_text("hi\x00", "en")
    with pytest.raises(ValueError):
        normalize_tts_text("hi", "fr")


def test_safety_checks_actual_normalized_text_on_hits_and_caches_substitution():
    class RecordingSafety(SafetyService):
        def __init__(self):
            self.seen = []
            super().__init__(InMemorySafetyEventStore())

        async def check(self, text, context, **kwargs):
            self.seen.append(text)
            return await super().check(text, context, **kwargs)

    async def run():
        provider, safety = PCMProvider(), RecordingSafety()
        synth = service(provider, safety)
        request = TTSRequest("fees 50000", language="en")
        first = await synth.synthesize(request, CTX)
        second = await synth.synthesize(request, replace(CTX, turn_number=2))
        assert first.text != request.text and "50000" not in first.text
        assert provider.calls[0].text == first.text == second.text
        assert second.cache_hit and len(provider.calls) == 1
        assert safety.seen[-1] == second.text
        assert len(safety.store.entries) >= 2
        assert all(len(frame) == 160 for frame in first.mulaw_frames)
        assert synth.hits == synth.misses == synth.stores == 1
        assert second.synthesis_ms == second.rtf == 0

    asyncio.run(run())


def test_normalization_cannot_remove_raw_money_safety_and_audit_failure_uses_safe_line():
    class BrokenStore:
        async def record(self, context, events):
            raise OSError("synthetic failure")

    async def run():
        provider = PCMProvider()
        result = await service(provider, SafetyService(BrokenStore())).synthesize(
            TTSRequest("salary 50000"), CTX
        )
        assert not result.audit_ok
        assert "50000" not in provider.calls[0].text

    asyncio.run(run())


def test_voice_language_format_and_model_identity_separate_cache():
    async def run():
        provider = PCMProvider()
        synth = service(provider)
        request = TTSRequest("Hello", language="en")
        for item in (
            request,
            replace(request, voice="male"),
            replace(request, language="hi"),
            replace(request, format=AudioFormat(16000)),
        ):
            await synth.synthesize(item, CTX)
        synth.model_version = "test-v2"
        await synth.synthesize(request, CTX)
        assert len(provider.calls) == 5

    asyncio.run(run())


def test_lru_capacity_and_byte_bounds():
    async def run():
        provider = PCMProvider()
        synth = service(provider, cache_entries=2, cache_bytes=640)
        for text in ("one", "two", "one", "three", "two"):
            await synth.synthesize(TTSRequest(text), CTX)
        assert len(provider.calls) == 4 and synth._bytes <= 640 and len(synth._cache) == 2

    asyncio.run(run())


def test_cancellation_kills_last_waiter_without_storing_audio():
    async def run():
        provider = PCMProvider(wait=True)
        synth = service(provider)
        task = asyncio.create_task(synth.synthesize(TTSRequest("hello"), CTX))
        await provider.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.cancelled and not synth._cache and not synth._flights
        assert synth._pending == 0

    asyncio.run(run())


def test_singleflight_and_cancellation_preserve_other_waiter():
    async def run():
        provider = PCMProvider(wait=True)
        synth = service(provider)
        first = asyncio.create_task(synth.synthesize(TTSRequest("hello"), CTX))
        await provider.started.wait()
        second = asyncio.create_task(synth.synthesize(TTSRequest("hello"), CTX))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not provider.cancelled
        provider.release.set()
        result = await second
        assert (
            result.cache_status == "coalesced"
            and len(provider.calls) == 1
            and synth.stores == 1
        )

    asyncio.run(run())


def test_timeout_and_admission_bound_cleanup():
    async def run():
        provider = PCMProvider(wait=True)
        synth = service(provider, timeout_secs=0.02, max_pending=1)
        first = asyncio.create_task(synth.synthesize(TTSRequest("hello"), CTX))
        await provider.started.wait()
        with pytest.raises(ValueError, match="admission"):
            await synth.synthesize(TTSRequest("other"), CTX)
        with pytest.raises(TimeoutError):
            await first
        assert provider.cancelled and synth._pending == 0 and not synth._flights

    asyncio.run(run())


@pytest.mark.parametrize("audio", [b"", b"x", b"\0\0" * 8001])
def test_invalid_or_oversized_provider_output_never_cached(audio):
    async def run():
        synth = service(PCMProvider(audio), max_audio_secs=1)
        with pytest.raises(ValueError):
            await synth.synthesize(TTSRequest("hello"), CTX)
        assert not synth._cache

    asyncio.run(run())


def test_request_bytes_and_expansion_bounds_reject_before_provider():
    async def run():
        provider = PCMProvider()
        synth = service(provider, max_text_bytes=128)
        with pytest.raises(ValueError):
            await synth.synthesize(TTSRequest("ક" * 50), CTX)
        with pytest.raises(ValueError, match="Normalized"):
            await synth.synthesize(TTSRequest("9" * 80), CTX)
        assert not provider.calls

    asyncio.run(run())


def test_distinct_requests_respect_synthesis_concurrency():
    class CountProvider(PCMProvider):
        def __init__(self):
            super().__init__()
            self.active = self.peak = 0

        async def synthesize(self, request):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.01)
                return await super().synthesize(request)
            finally:
                self.active -= 1

    async def run():
        provider = CountProvider()
        synth = service(provider, max_concurrency=2)
        await asyncio.gather(
            *(
                synth.synthesize(TTSRequest(text), CTX)
                for text in ("one", "two", "three", "four")
            )
        )
        assert provider.peak == 2

    asyncio.run(run())


def test_metadata_and_provider_default_voice_are_part_of_actual_cache_identity():
    async def run():
        provider = PCMProvider()
        provider.voice = "male"
        synth = service(provider)
        first = await synth.synthesize(
            TTSRequest("hello", metadata={"style": "one", "other": "two"}), CTX
        )
        hit = await synth.synthesize(
            TTSRequest("hello", metadata={"other": "two", "style": "one"}), CTX
        )
        miss = await synth.synthesize(TTSRequest("hello", metadata={"style": "other"}), CTX)
        assert provider.calls[0].voice == "male"
        assert first.cache_status == miss.cache_status == "miss" and hit.cache_hit
        assert len(provider.calls) == 2

    asyncio.run(run())


def test_metadata_is_copied_before_safety_await():
    class ChangingSafety(SafetyService):
        async def check(self, text, context, **kwargs):
            metadata["style"] = "changed"
            return await super().check(text, context, **kwargs)

    metadata = {"style": "original"}

    async def run():
        provider = PCMProvider()
        await service(provider, ChangingSafety()).synthesize(
            TTSRequest("hello", metadata=metadata), CTX
        )
        assert provider.calls[0].metadata == {"style": "original"}

    asyncio.run(run())


@pytest.mark.parametrize("control", ["\u2060", "\u00ad", "\u200e", "\u200f", "\u061c"])
def test_format_controls_cannot_hide_fee_claim_before_digit_spelling(control):
    async def run():
        provider = PCMProvider()
        synth = service(provider)
        with pytest.raises(ValueError, match="Control characters"):
            await synth.synthesize(TTSRequest(f"f{control}ees 25000", language="en"), CTX)
        assert provider.calls == []
        assert synth.stores == synth.misses == 0

    asyncio.run(run())
