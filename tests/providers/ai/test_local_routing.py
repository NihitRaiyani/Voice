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
    split_code_mix_spans,
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


def test_split_code_mix_spans_uses_one_strong_internal_gap():
    assert tuple(len(part) // 32 for part in split_code_mix_spans(mixed_audio())) == (1000, 1200)
    assert split_code_mix_spans(pcm(2200)) == (pcm(2200),)
    assert split_code_mix_spans(pcm(400) + pcm(700, 0) + pcm(400)) == (
        pcm(400) + pcm(700, 0) + pcm(400),
    )


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

def test_mixed_route_decodes_spans_and_uses_whisper_only_after_indic_abstains():
    async def run():
        indic = StubSTT(STTResult("મારે કાલે", "gu"))
        whisper = StubSTT(STTResult("an appointment at six", "en"))
        provider = LocalLanguageSTT(indic, whisper)
        result = await provider.transcribe(request(mixed_audio(), "gu-en"))
        assert result == STTResult("મારે કાલે an appointment at six", "gu-en")
        assert [len(row.audio) // 32 for row in indic.requests] == [1000]
        assert [row.language_hint for row in indic.requests] == ["gu"]
        assert [len(row.audio) // 32 for row in whisper.requests] == [1200]
        assert whisper.requests[0].language_hint is None

    asyncio.run(run())


def test_mixed_tail_uses_credible_whisper_english_over_indic_script_gibberish():
    async def run():
        indic = StubSTT(
            STTResult("मुझे जानकारी चाहिए", "hi"),
        )
        whisper = StubSTT(STTResult("when can I visit tomorrow", "en"))
        result = await LocalLanguageSTT(indic, whisper).transcribe(
            request(mixed_audio(), "hi-en")
        )
        assert result == STTResult("मुझे जानकारी चाहिए when can I visit tomorrow", "hi-en")

    asyncio.run(run())


def test_mixed_tail_keeps_indic_when_whisper_has_no_credible_english():
    async def run():
        indic = StubSTT(STTResult("मुझे जानकारी चाहिए", "hi"), STTResult("हाँ ठीक", "hi"))
        whisper = StubSTT(STTResult("uh", "en"))
        result = await LocalLanguageSTT(indic, whisper).transcribe(
            request(mixed_audio(), "hi-en")
        )
        assert result == STTResult("मुझे जानकारी चाहिए हाँ ठीक", "hi-en")
        assert len(whisper.requests) == 1

    asyncio.run(run())


def test_mixed_tail_keeps_indic_when_whisper_detects_non_english():
    async def run():
        indic = StubSTT(STTResult("मुझे जानकारी चाहिए", "hi"), STTResult("हाँ ठीक", "hi"))
        whisper = StubSTT(STTResult("plausible English hallucination", "hi"))
        result = await LocalLanguageSTT(indic, whisper).transcribe(
            request(mixed_audio(), "hi-en")
        )
        assert result == STTResult("मुझे जानकारी चाहिए हाँ ठीक", "hi-en")

    asyncio.run(run())


def test_one_letter_indic_is_abstention_but_short_acknowledgement_is_not():
    async def run():
        fallback = LocalLanguageSTT(
            StubSTT(STTResult("व", "hi")), StubSTT(STTResult("yes", "en"))
        )
        assert (await fallback.transcribe(request(pcm(1000), "hi-en"))).text == "yes"
        assert fallback.whisper.requests[0].language_hint is None
        indic, whisper = StubSTT(STTResult("हाँ", "hi")), StubSTT()
        accepted = await LocalLanguageSTT(indic, whisper).transcribe(
            request(pcm(1000), "hi-en")
        )
        assert accepted == STTResult("हाँ", "hi-en")
        assert not whisper.requests

    asyncio.run(run())


def test_unusable_mixed_head_rejects_whisper_text_not_identified_as_english():
    async def run():
        indic = StubSTT(STTResult("व", "hi"))
        whisper = StubSTT(
            STTResult("appointment tomorrow", "en"),
            STTResult("plausible English hallucination", "hi"),
        )
        result = await LocalLanguageSTT(indic, whisper).transcribe(
            request(mixed_audio(), "hi-en")
        )
        assert result == STTResult("appointment tomorrow", "hi-en")
        assert [row.language_hint for row in whisper.requests] == [None, None]

    asyncio.run(run())


def test_provider_failure_never_triggers_the_other_model():
    async def run():
        indic = StubSTT(ProviderUnavailable("unavailable"))
        whisper = StubSTT(STTResult("invented fallback", "en"))
        with pytest.raises(ProviderUnavailable):
            await LocalLanguageSTT(indic, whisper).transcribe(
                request(pcm(1000), "hi-en")
            )
        assert not whisper.requests

    asyncio.run(run())


def test_mixed_fast_path_normalizes_reviewed_loanword_without_whisper():
    async def run():
        indic = StubSTT(STTResult("अपॉइंटमेंट कन्फर्म", "hi"))
        whisper = StubSTT()
        result = await LocalLanguageSTT(indic, whisper).transcribe(
            request(pcm(1000), "hi-en")
        )
        assert result == STTResult("appointment confirm", "hi-en")
        assert not whisper.requests

    asyncio.run(run())
