"""One local multilingual ASR decode for Gujarati, Hindi, English and code-mix.

One selected Whisper model receives every audio byte. Mixed turns may use a
bounded speech batch; exact loanwords are normalized before the controller.
"""

import re
import unicodedata

from roma.providers.ai.contracts import STTProvider, STTRequest, STTResult
from roma.providers.ai.stt import normalize_transcript

ROUTER_VERSION = "whisper-turbo-batched-v2"
_LOANWORDS = {
    "gu": {
        "અપોઇન્ટમેન્ટ": "appointment",
        "અપોઈન્ટમેન્ટ": "appointment",
        "એપોઇન્ટમેન્ટ": "appointment",
        "બુકિંગ": "booking",
        "બુકીંગ": "booking",
        "કન્ફર્મ": "confirm",
        "કન્ફર્મેશન": "confirmation",
        "કેન્સલ": "cancel",
        "ટુમોરો": "tomorrow",
        "વિઝિટ": "visit",
        "ટાઈમ": "time",
        "ટાઇમ": "time",
        "ડેટ": "date",
    },
    "hi": {
        "अपॉइंटमेंट": "appointment",
        "अपोइंटमेंट": "appointment",
        "अपाइंटमेंट": "appointment",
        "अपॉइंटमेण्ट": "appointment",
        "बुकिंग": "booking",
        "कन्फर्म": "confirm",
        "कन्फ़र्म": "confirm",
        "कन्फर्मेशन": "confirmation",
        "कैंसल": "cancel",
        "कैंसिल": "cancel",
        "केंसल": "cancel",
        "टुमॉरो": "tomorrow",
        "टुमारो": "tomorrow",
        "टुमोरो": "tomorrow",
        "विजिट": "visit",
        "विज़िट": "visit",
        "टाइम": "time",
        "डेट": "date",
    },
}
_INDIC_TOKEN = re.compile(r"[\w\u0900-\u097f\u0a80-\u0aff]+")


def normalize_domain_loanwords(text: str, language: str) -> str:
    """Canonicalize only exact, reviewed booking tokens; never fuzzy-match names."""
    normalized = normalize_transcript(text)
    lexicon = _LOANWORDS.get(language, {})

    def replace_script_run(match: re.Match[str]) -> str:
        output: list[str] = []
        token: list[str] = []

        def flush_token() -> None:
            if token:
                value = "".join(token)
                output.append(lexicon.get(value, value))
                token.clear()

        for char in match.group():
            if unicodedata.category(char)[0] in "LMN" or char == "_":
                token.append(char)
            else:
                flush_token()
                output.append(char)
        flush_token()
        return "".join(output)

    return _INDIC_TOKEN.sub(replace_script_run, normalized)


class LocalLanguageSTT:
    """One multilingual decoder and an exact text-boundary normalizer."""

    def __init__(self, provider: STTProvider):
        self.provider = provider

    async def transcribe(self, request: STTRequest) -> STTResult:
        if request.language_hint is not None and not isinstance(request.language_hint, str):
            raise ValueError("Unsupported local ASR language route")
        hint = request.language_hint.replace("_", "-").lower() if request.language_hint else None
        aliases = {"gu-en": "gu", "hi-en": "hi", "gu-in": "gu", "hi-in": "hi", "en-in": "en", "en-us": "en"}
        base = aliases.get(hint or "", hint)
        if base not in {None, "gu", "hi", "en"}:
            raise ValueError("Unsupported local ASR language route")
        result = await self.provider.transcribe(
            STTRequest(request.audio, request.format, base, {**request.metadata, **({"local_language_route": hint} if hint else {})})
        )
        return STTResult(
            normalize_domain_loanwords(result.text, base or result.language or ""),
            hint or result.language,
            result.confidence,
        )


__all__ = ["LocalLanguageSTT", "normalize_domain_loanwords", "ROUTER_VERSION"]
