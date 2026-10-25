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
        assert safety.seen[-1] == second.canonical_text
        assert second.spoken_language == "hi" and second.requested_language == "en"
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
            await synth.synthesize(TTSRequest("slot 9999 " * 6), CTX)
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


@pytest.mark.parametrize("language", ["hi", "gu", "en"])
@pytest.mark.parametrize(
    "text,category",
    [
        ("fees 50000", "FEE"),
        ("discount", "DISCOUNT"),
        ("salary 50000", "SALARY"),
        ("placement guaranteed", "PLACEMENT"),
        ("Google certificate", "CERT"),
    ],
)
def test_every_safety_category_uses_audited_pinned_hindi_speech(language, text, category):
    from roma.domain.safety.lexicon import SUBSTITUTIONS, BlockCategory
    from roma.domain.safety.pronunciation import pronunciation_for

    async def run():
        provider, safety = PCMProvider(), SafetyService()
        synth = service(provider, safety)
        first = await synth.synthesize(TTSRequest(text, language=language), CTX)
        second = await synth.synthesize(TTSRequest(text, language=language), CTX)
        canonical = SUBSTITUTIONS[BlockCategory(category)]
        rendered = pronunciation_for(canonical)
        assert first.canonical_text == canonical
        assert first.text == rendered.spoken_text
        assert first.spoken_language == provider.calls[0].language == "hi"
        assert first.requested_language == language and first.voice == "female"
        assert first.pronunciation_version == rendered.version
        assert first.canonical_sha256 == rendered.canonical_sha256
        assert first.audit_ok and second.audit_ok and second.cache_hit
        events = [event for _, event in safety.store.entries]
        assert sum(event.category == category for event in events) == 2
        assert sum(event.category == "PRONUNCIATION" for event in events) == 2
        assert all(event.replacement_used == canonical for event in events)
        assert len(provider.calls) == 1

    asyncio.run(run())


def test_all_approved_renderings_share_actual_hindi_cache_across_requested_languages():
    from roma.domain.safety.pronunciation import PRONUNCIATIONS

    async def run():
        provider = PCMProvider()
        synth = service(provider)
        for item in PRONUNCIATIONS:
            results = [
                await synth.synthesize(TTSRequest(item.canonical_text, language=language), CTX)
                for language in ("hi", "gu", "en", "hi")
            ]
            assert [r.cache_status for r in results] == ["miss", "hit", "hit", "hit"]
            assert all(r.spoken_language == "hi" for r in results)
            assert all(r.text == item.spoken_text for r in results)
        assert synth.misses == synth.stores == 6 and synth.hits == 18
        assert len(provider.calls) == 6

    asyncio.run(run())


def test_rendering_uses_actual_provider_identity_voice_and_version_in_cache(monkeypatch):
    from roma.domain.safety import pronunciation as bindings

    class IdentityProvider(PCMProvider):
        def __init__(self):
            super().__init__()
            self.identity = "actual-hi-v1"
            self.identity_requests = []

        async def cache_identity(self, request):
            self.identity_requests.append(request)
            return self.identity

    async def run():
        provider = IdentityProvider()
        synth = service(provider)
        item = bindings.PRONUNCIATIONS[0]
        request = TTSRequest(item.canonical_text, language="gu")
        first = await synth.synthesize(request, CTX)
        provider.identity = "actual-hi-v2"
        second = await synth.synthesize(request, CTX)
        third = await synth.synthesize(replace(request, voice="male"), CTX)
        updated = replace(item, version="roma-safety-pronunciation-hi-v2")
        monkeypatch.setattr(bindings, "PRONUNCIATION_VERSION", updated.version)
        monkeypatch.setattr(bindings, "PRONUNCIATIONS", (updated, *bindings.PRONUNCIATIONS[1:]))
        fourth = await synth.synthesize(request, CTX)
        assert all(r.cache_status == "miss" for r in (first, second, third, fourth))
        assert all(r.language == "hi" for r in provider.identity_requests)
        assert first.model_version == "actual-hi-v1"
        assert second.model_version == "actual-hi-v2" and third.voice == "male"
        assert fourth.pronunciation_version == updated.version

    asyncio.run(run())


def test_rendering_audit_outage_uses_pinned_hard_fail_without_recursive_audit():
    from roma.domain.safety.lexicon import HARD_FAIL_LINE
    from roma.domain.safety.pronunciation import pronunciation_for

    class RenderingBrokenStore:
        def __init__(self):
            self.calls = []

        async def record(self, context, events):
            self.calls.append(events)
            if events[0].category == "PRONUNCIATION":
                raise OSError("synthetic rendering audit outage")

    async def run():
        provider, store = PCMProvider(), RenderingBrokenStore()
        result = await service(provider, SafetyService(store)).synthesize(
            TTSRequest("fees 50000", language="gu"), CTX
        )
        assert not result.audit_ok
        assert result.text == pronunciation_for(HARD_FAIL_LINE).spoken_text
        assert provider.calls[0].text == result.text and provider.calls[0].language == "hi"
        assert len(store.calls) == 2
        assert store.calls[0][0].category == "FEE"
        assert store.calls[1][0].category == "PRONUNCIATION"

    asyncio.run(run())


def test_cancellation_during_rendering_audit_never_releases_audio():
    class WaitingStore:
        def __init__(self):
            self.started = asyncio.Event()

        async def record(self, context, events):
            if events[0].category == "PRONUNCIATION":
                self.started.set()
                await asyncio.Event().wait()

    async def run():
        provider, store = PCMProvider(), WaitingStore()
        synth = service(provider, SafetyService(store))
        task = asyncio.create_task(synth.synthesize(TTSRequest("fees 50000"), CTX))
        await store.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not provider.calls and not synth._cache and not synth._flights
        assert synth._pending == 0

    asyncio.run(run())


def test_missing_or_tampered_rendering_never_looks_up_model_or_cache(monkeypatch):
    from roma.domain.safety import pronunciation as bindings

    async def run():
        provider = PCMProvider()
        synth = service(provider)
        item = bindings.PRONUNCIATIONS[0]
        monkeypatch.setattr(
            bindings, "PRONUNCIATIONS", (replace(item, spoken_text="फीस 50000"),)
        )
        with pytest.raises(ValueError, match="binding"):
            await synth.synthesize(TTSRequest("fees 50000"), CTX)
        monkeypatch.setattr(bindings, "PRONUNCIATIONS", ())
        with pytest.raises(ValueError, match="reviewed pronunciation"):
            await synth.synthesize(TTSRequest("fees 50000"), CTX)
        assert not provider.calls and not synth._cache and synth.misses == 0

    asyncio.run(run())


@pytest.mark.parametrize("number", ["9876543210", "123 456 7890", "+91 98765 43210"])
def test_private_numeric_forms_never_reach_provider_cache_or_logs(number, caplog):
    async def run():
        provider = PCMProvider()
        synth = service(provider)
        with pytest.raises(ValueError):
            await synth.synthesize(TTSRequest(number), CTX)
        assert provider.calls == [] and not synth._cache and not synth._flights
        assert synth.misses == synth.stores == 0
        assert number not in caplog.text

    asyncio.run(run())


def test_actual_expansion_is_safety_checked_before_audio_and_on_cache_hits(monkeypatch):
    from roma.services import tts_service

    class RecordingSafety(SafetyService):
        def __init__(self):
            super().__init__()
            self.seen = []

        async def check(self, text, context, **kwargs):
            self.seen.append(text)
            return await super().check(text, context, **kwargs)

    async def run():
        provider, safety = PCMProvider(), RecordingSafety()
        synth = service(provider, safety)
        # A future vocabulary expansion cannot create a financial claim after the
        # first screen and silently send that changed text to inference/cache.
        monkeypatch.setattr(tts_service, "normalize_tts_text", lambda *_: "fees 500")
        first = await synth.synthesize(TTSRequest("appointment"), CTX)
        second = await synth.synthesize(TTSRequest("appointment"), CTX)
        assert safety.seen == ["appointment", "fees 500", "appointment", "fees 500"]
        assert "500" not in first.text and second.cache_hit
        assert first.pronunciation_version and first.canonical_sha256 and first.spoken_sha256
        assert provider.calls[0].text == first.text and provider.calls[0].language == "hi"
        assert sum(event.category == "FEE" for _, event in safety.store.entries) == 2

    asyncio.run(run())


def test_normalization_version_partitions_cached_audio(monkeypatch):
    from roma.domain.audio import speech_normalization
    from roma.services import tts_service

    assert tts_service.NORMALIZATION_VERSION == speech_normalization.NORMALIZATION_VERSION
    assert tts_service.NORMALIZATION_VERSION == "roma-tts-normalization-v2"

    async def run():
        provider = PCMProvider()
        synth = service(provider)
        first = await synth.synthesize(TTSRequest("slot 23", language="en"), CTX)
        hit = await synth.synthesize(TTSRequest("slot 23", language="en"), CTX)
        monkeypatch.setattr(tts_service, "NORMALIZATION_VERSION", "synthetic-next-version")
        changed = await synth.synthesize(TTSRequest("slot 23", language="en"), CTX)
        assert first.cache_status == changed.cache_status == "miss" and hit.cache_hit
        assert len(provider.calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("language,day,month,year", [
    ("en", "five", "November", "twenty twenty six"),
    ("hi", "पाँच", "नवंबर", "बीस छब्बीस"),
    ("gu", "પાંચ", "નવેમ્બર", "વીસ છવ્વીસ"),
])
def test_calendar_year_speech_survives_actual_final_safety(language, day, month, year):
    async def run():
        provider, safety = PCMProvider(), SafetyService()
        result = await service(provider, safety).synthesize(
            TTSRequest("2026-11-05 10:30 AM", language=language), CTX
        )
        assert day in result.text and month in result.text and year in result.text
        assert result.pronunciation_version is None
        assert result.spoken_language == language and result.audit_ok
        assert provider.calls[0].text == result.text
        assert not safety.store.entries

    asyncio.run(run())
