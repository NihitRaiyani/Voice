import asyncio
from array import array

import pytest
from roma.providers.ai.contracts import (
    AudioFormat,
    ProviderUnavailable,
    STTRequest,
    STTResult,
)
from roma.providers.ai.local_routing import (
    LocalLanguageSTT,
    normalize_domain_loanwords,
)


def pcm(milliseconds: int, amplitude: int = 1000) -> bytes:
    return array("h", [amplitude] * (16 * milliseconds)).tobytes()


def mixed_audio() -> bytes:
    return pcm(1000) + pcm(700, 0) + pcm(1200)


class StubSTT:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    async def transcribe(self, request):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def request(audio: bytes = b"\0\0", language: str = "hi-en") -> STTRequest:
    return STTRequest(audio, AudioFormat(16000), language)


@pytest.mark.parametrize(
    "text,language,expected",
    [
        ("અપોઇન્ટમેન્ટ કન્ફર્મ કરો", "gu", "appointment confirm કરો"),
        ("अपॉइंटमेंट कैंसल करो", "hi", "appointment cancel करो"),
        ("બુકિંગ ટુમોરો રાખો", "gu", "booking tomorrow રાખો"),
        ("मेरा नाम कन्फर्मेशन नहीं है", "hi", "मेरा नाम confirmation नहीं है"),
        ("अपॉइंटमेंट। फिर कल", "hi", "appointment। फिर कल"),
        ("અપોઇન્ટમેન્ટ, કન્ફર્મ.", "gu", "appointment, confirm."),
        ("અપોઇન્ટમેન્ટવાળો શબ્દ", "gu", "અપોઇન્ટમેન્ટવાળો શબ્દ"),
    ],
)
def test_domain_loanwords_are_exact_tokens(text, language, expected):
    assert normalize_domain_loanwords(text, language) == expected


def test_monolingual_routes_remain_direct():
    async def run():
        indic = StubSTT(STTResult("હા", "gu"), STTResult("हाँ", "hi"))
        whisper = StubSTT(STTResult("yes", "en"))
        provider = LocalLanguageSTT(indic, whisper)
        assert (await provider.transcribe(request(language="gu"))).text == "હા"
        assert (await provider.transcribe(request(language="hi"))).text == "हाँ"
        assert (await provider.transcribe(request(language="en"))).text == "yes"
        assert [row.language_hint for row in indic.requests] == ["gu", "hi"]
        assert [row.language_hint for row in whisper.requests] == ["en"]

    asyncio.run(run())

@pytest.mark.parametrize("language,base,text,expected", [
    ("gu-en", "gu", "અપોઈન્ટમેન્ટ કન્ફર્મ કરી દેજો", "appointment confirm કરી દેજો"),
    ("hi-en", "hi", "अपॉइंटमेंट कन्फर्म कर दो", "appointment confirm कर दो"),
    ("gu", "gu", "વિઝિટ ટાઈમ ડેટ", "visit time date"),
    ("hi", "hi", "विजिट टाइम डेट", "visit time date"),
])
def test_indic_and_mixed_turns_use_one_full_audio_pass(language, base, text, expected):
    async def run():
        indic, whisper = StubSTT(STTResult(text, base, 0.7)), StubSTT()
        audio = mixed_audio()
        original = request(audio, language)
        result = await LocalLanguageSTT(indic, whisper).transcribe(original)
        assert result == STTResult(expected, language, 0.7)
        assert len(indic.requests) == 1 and not whisper.requests
        assert indic.requests[0].audio is audio
        assert indic.requests[0].language_hint == base
        assert indic.requests[0].format == original.format
        assert indic.requests[0].metadata == original.metadata
    asyncio.run(run())


@pytest.mark.parametrize("text", ["", "व", "हाँ"])
def test_no_whisper_cascade_for_empty_short_or_acknowledgement(text):
    async def run():
        indic, whisper = StubSTT(STTResult(text, "hi")), StubSTT()
        result = await LocalLanguageSTT(indic, whisper).transcribe(request(mixed_audio()))
        assert result.text == text and not whisper.requests
        assert len(indic.requests) == 1
    asyncio.run(run())


def test_provider_failure_never_triggers_the_other_model():
    async def run():
        indic = StubSTT(ProviderUnavailable("unavailable"))
        whisper = StubSTT()
        with pytest.raises(ProviderUnavailable):
            await LocalLanguageSTT(indic, whisper).transcribe(request(mixed_audio()))
        assert not whisper.requests
    asyncio.run(run())


@pytest.mark.parametrize("text", [
    "કાલે આવું છું", "कल आऊंगा", "10 AM e call karjo", "SEO Google Ads Digital Marketing",
    "અપોઇન્ટમેન્ટવાળો", "myઅપોઇન્ટમેન્ટname", "अपॉइंटमेंटवाला", "અપોઇન્ટમેન્ટ_નામ",
])
def test_native_dates_entities_compounds_and_existing_english_are_preserved(text):
    for language in ("gu", "hi"):
        assert normalize_domain_loanwords(text, language) == text
