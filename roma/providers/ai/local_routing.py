"""Explicit cache-only language composition for the local speech lab.

Monolingual routes remain direct. Explicit Gujarati/Hindi-English routes may
split one strong phrase boundary, use Whisper language detection on the tail,
and retain Indic when that tail is not credible English. Provider errors are
never fallback signals.
"""

from __future__ import annotations

import math
import re
import sys
import unicodedata
from array import array

from roma.providers.ai.contracts import STTRequest, STTResult
from roma.providers.ai.stt import normalize_transcript

_FRAME_SAMPLES = 320  # 20 ms at 16 kHz
_FRAME_BYTES = _FRAME_SAMPLES * 2
_MIN_GAP_FRAMES = 30  # 600 ms
_MIN_SIDE_FRAMES = 40  # 800 ms
_MIXED = {"gu-en": "gu", "hi-en": "hi"}
_SCRIPT = {
    "gu": ("\u0a80", "\u0aff"),
    "hi": ("\u0900", "\u097f"),
}
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
    },
    "hi": {
        "अपॉइंटमेंट": "appointment",
        "अपोइंटमेंट": "appointment",
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
    },
}
_INDIC_TOKEN = re.compile(r"[\u0900-\u097f]+|[\u0a80-\u0aff]+")
_ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_DOMAIN_ENGLISH = frozenset({"appointment", "booking", "confirm", "confirmation", "cancel", "tomorrow"})
_SHORT_ENGLISH = frozenset({"yes", "no", "ok", "okay", "hello", "thanks", "bye"})


def _rms_frames(pcm: bytes) -> list[float]:
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
        return []
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return [
        math.sqrt(
            sum(value * value for value in samples[start : start + _FRAME_SAMPLES])
            / _FRAME_SAMPLES
        )
        for start in range(0, len(samples) - _FRAME_SAMPLES + 1, _FRAME_SAMPLES)
    ]


def split_code_mix_spans(pcm: bytes) -> tuple[bytes, ...]:
    """Split at one strong internal low-energy run; never infer language from energy."""
    energy = _rms_frames(pcm)
    if len(energy) < 2 * _MIN_SIDE_FRAMES + _MIN_GAP_FRAMES:
        return (pcm,)
    ordered = sorted(energy)
    floor = ordered[len(ordered) // 10]
    high = ordered[9 * len(ordered) // 10]
    if high <= floor:
        return (pcm,)
    threshold = floor + 0.18 * (high - floor)
    runs: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate([*energy, math.inf]):
        if value < threshold and start is None:
            start = index
        elif value >= threshold and start is not None:
            runs.append((start, index))
            start = None
    candidates = [
        (start, end)
        for start, end in runs
        if end - start >= _MIN_GAP_FRAMES
        and start >= _MIN_SIDE_FRAMES
        and len(energy) - end >= _MIN_SIDE_FRAMES
    ]
    if not candidates:
        return (pcm,)
    start, end = max(candidates, key=lambda run: (run[1] - run[0], run[0]))
    return pcm[: start * _FRAME_BYTES], pcm[end * _FRAME_BYTES :]


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
            if unicodedata.category(char)[0] in "LM":
                token.append(char)
            else:
                flush_token()
                output.append(char)
        flush_token()
        return "".join(output)

    return _INDIC_TOKEN.sub(replace_script_run, normalized)


def _usable_indic(text: str, language: str) -> bool:
    start, end = _SCRIPT[language]
    return (
        sum(
            start <= char <= end and unicodedata.category(char)[0] in "LM"
            for char in normalize_transcript(text)
        )
        >= 2
    )


def _credible_english(text: str) -> bool:
    words = [word.casefold() for word in _ENGLISH_WORD.findall(normalize_transcript(text))]
    return len(words) >= 2 or bool(set(words) & (_DOMAIN_ENGLISH | _SHORT_ENGLISH))


def _usable_english(result: STTResult) -> bool:
    return result.language == "en" and _credible_english(result.text)


class LocalLanguageSTT:
    def __init__(self, indic, whisper):
        self.indic, self.whisper = indic, whisper

    async def transcribe(self, request: STTRequest) -> STTResult:
        hint = request.language_hint.replace("_", "-").lower() if request.language_hint else None
        if hint in {"en", "en-in", "en-us"}:
            return await self.whisper.transcribe(request)
        base = _MIXED.get(hint or "")
        if base is None:
            return await self.indic.transcribe(request)

        spans = split_code_mix_spans(request.audio)
        if len(spans) == 2:
            head, tail = spans
            indic_head = await self.indic.transcribe(
                STTRequest(head, request.format, base, request.metadata)
            )
            english_tail = await self.whisper.transcribe(
                STTRequest(tail, request.format, None, request.metadata)
            )
            head_text = (
                normalize_domain_loanwords(indic_head.text, base)
                if _usable_indic(indic_head.text, base)
                else ""
            )
            if not head_text:
                english_head = await self.whisper.transcribe(
                    STTRequest(head, request.format, None, request.metadata)
                )
                if _usable_english(english_head):
                    head_text = normalize_transcript(english_head.text)
            if _usable_english(english_tail):
                tail_text = normalize_transcript(english_tail.text)
            else:
                indic_tail = await self.indic.transcribe(
                    STTRequest(tail, request.format, base, request.metadata)
                )
                tail_text = (
                    normalize_domain_loanwords(indic_tail.text, base)
                    if _usable_indic(indic_tail.text, base)
                    else ""
                )
            return STTResult(normalize_transcript(f"{head_text} {tail_text}"), hint)

        parts = []
        for pcm in spans:
            indic_request = STTRequest(pcm, request.format, base, request.metadata)
            result = await self.indic.transcribe(indic_request)
            indic_text = (
                normalize_domain_loanwords(result.text, base)
                if _usable_indic(result.text, base)
                else ""
            )
            if indic_text:
                parts.append(indic_text)
                continue
            english = await self.whisper.transcribe(
                STTRequest(pcm, request.format, None, request.metadata)
            )
            if _usable_english(english):
                parts.append(normalize_transcript(english.text))
        return STTResult(normalize_transcript(" ".join(parts)), hint)


__all__ = ["LocalLanguageSTT", "normalize_domain_loanwords", "split_code_mix_spans"]
