"""Safety-audited, bounded local phrase synthesis and a process-local LRU.

Separate from the retained Sarvam/PhraseCache live path. PCM is cached only after
successful inference/validation. Every request, including hits, crosses safety.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date
from time import perf_counter
from typing import Literal

from roma.core.turn_epoch import current_turn_epoch, require_current_epoch
from roma.domain.audio.g711 import encode
from roma.domain.audio.pcm import PCMFramer, validate_pcm16
from roma.domain.audio.resampling import PCMResampler
from roma.domain.safety.audit import SafetyContext
from roma.domain.safety.pronunciation import pronunciation_for
from roma.providers.ai.contracts import TTSProvider, TTSRequest, TTSResult
from roma.providers.ai.indic_tts import language_code, validate_request
from roma.services.safety_service import SafetyService

NORMALIZATION_VERSION = "roma-tts-normalization-v1"
_DIGITS = {
    "en": "zero one two three four five six seven eight nine".split(),
    "hi": "शून्य एक दो तीन चार पाँच छह सात आठ नौ".split(),
    "gu": "શૂન્ય એક બે ત્રણ ચાર પાંચ છ સાત આઠ નવ".split(),
}
_MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "hi": "जनवरी फरवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split(),
    "gu": "જાન્યુઆરી ફેબ્રુઆરી માર્ચ એપ્રિલ મે જૂન જુલાઈ ઓગસ્ટ સપ્ટેમ્બર ઓક્ટોબર નવેમ્બર ડિસેમ્બર".split(),
}
_TERMS = {
    "en": {"SEO": "S E O", "SEM": "S E M", "PPC": "P P C", "CRM": "C R M"},
    "hi": {
        "SEO": "एस ई ओ",
        "SEM": "एस ई एम",
        "PPC": "पी पी सी",
        "CRM": "सी आर एम",
        "Digital Marketing": "डिजिटल मार्केटिंग",
        "Google Ads": "गूगल ऐड्स",
    },
    "gu": {
        "SEO": "એસ ઈ ઓ",
        "SEM": "એસ ઈ એમ",
        "PPC": "પી પી સી",
        "CRM": "સી આર એમ",
        "Digital Marketing": "ડિજિટલ માર્કેટિંગ",
        "Google Ads": "ગૂગલ એડ્સ",
    },
}


def normalize_tts_text(text: str, language: str) -> str:
    """NFC/whitespace, reviewed course terms, valid ISO/Indian dates and digit names.

    Numbers deliberately use individual digit names, including years; no inferred
    currency, unit, transliteration, language translation or ambiguous date guessing.
    Native fluency and cardinal-number pronunciation require separate review.
    """
    lang = language_code(language)
    text = unicodedata.normalize("NFC", text)
    # A tokenizer can silently discard format controls and rejoin an obfuscated
    # claim after digits have been spelled out. Reject those inputs before audio.
    if any(
        unicodedata.category(c) == "Cf" or (unicodedata.category(c) == "Cc" and not c.isspace())
        for c in text
    ):
        raise ValueError("Control characters are not accepted for speech")

    def digits(value: str) -> str:
        return " ".join(_DIGITS[lang][unicodedata.decimal(c)] for c in value)

    def render_date(match: re.Match) -> str:
        raw = match.group()
        if "-" in raw:
            year, month, day = (int(part) for part in raw.split("-"))
        else:
            day, month, year = (int(part) for part in raw.split("/"))
        date(year, month, day)  # Invalid dates must never become a different spoken date.
        return f"{digits(str(day))} {_MONTHS[lang][month - 1]} {digits(str(year))}"

    text = re.sub(
        r"(?<!\d)(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})(?!\d)",
        render_date,
        text,
    )
    for original, spoken in _TERMS[lang].items():
        text = re.sub(
            r"(?<!\w)" + re.escape(original) + r"(?!\w)", spoken, text, flags=re.IGNORECASE
        )
    text = re.sub(r"\d+", lambda match: digits(match.group()), text)
    return " ".join(text.split())


def build_mulaw_frames(audio, sample_rate):
    """Pickle-safe CPU boundary for the streaming profile; PCM output stays unchanged."""
    converter, framer = PCMResampler(sample_rate, 8000), PCMFramer(8000)
    frames = []
    for offset in range(0, len(audio), 3200):
        frames.extend(encode(frame) for frame in framer.feed(converter.feed(audio[offset:offset + 3200])))
    frames.extend(encode(frame) for frame in framer.feed(converter.finish()))
    if tail := framer.finish(pad=True):
        frames.append(encode(tail))
    return tuple(frames)


@dataclass(frozen=True)
class SynthesizedSpeech:
    text: str
    result: TTSResult
    mulaw_frames: tuple[bytes, ...]
    cache_status: Literal["hit", "miss", "coalesced"]
    synthesis_ms: float
    elapsed_ms: float
    rtf: float
    model_version: str
    audit_ok: bool
    canonical_text: str
    requested_language: str
    spoken_language: str
    voice: str
    pronunciation_version: str | None
    canonical_sha256: str | None
    spoken_canonical_text: str
    spoken_sha256: str | None
    safety_normalization_ms: float = 0.0

    @property
    def cache_hit(self) -> bool:
        return self.cache_status == "hit"


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0


class SpeechSynthesisService:
    def __init__(
        self,
        provider: TTSProvider,
        safety: SafetyService,
        *,
        model_version: str,
        default_voice: str | None = None,
        cache_entries: int = 64,
        cache_bytes: int = 8388608,
        max_concurrency: int = 1,
        max_pending: int = 16,
        timeout_secs: float = 60,
        max_text_bytes: int = 4096,
        max_audio_secs: float = 30,
    ) -> None:
        if not model_version or len(model_version) > 256:
            raise ValueError("An explicit bounded model version is required")
        if not 0 <= cache_entries <= 256 or not 0 <= cache_bytes <= 33554432:
            raise ValueError("Invalid TTS cache bound")
        if not 1 <= max_concurrency <= 4 or not 1 <= max_pending <= 64:
            raise ValueError("Invalid TTS concurrency bound")
        if not math.isfinite(timeout_secs) or not 0 < timeout_secs <= 180:
            raise ValueError("Invalid TTS timeout")
        if not math.isfinite(max_audio_secs) or not 0 < max_audio_secs <= 60:
            raise ValueError("Invalid TTS audio bound")
        if default_voice is None:
            default_voice = getattr(provider, "voice", "female")
        if (
            type(default_voice) is not str
            or default_voice not in {"female", "male"}
            or not 128 <= max_text_bytes <= 8192
        ):
            raise ValueError("Invalid TTS text bound or voice")
        self.provider, self.safety, self.model_version = provider, safety, model_version
        self.default_voice = default_voice
        self.cache_entries, self.cache_bytes = cache_entries, cache_bytes
        self.max_pending, self.timeout_secs = max_pending, timeout_secs
        self.max_text_bytes, self.max_audio_secs = max_text_bytes, max_audio_secs
        self._cache: OrderedDict[str, TTSResult] = OrderedDict()
        self._bytes = 0
        self._slots = asyncio.Semaphore(max_concurrency)
        self._flights: dict[tuple[str, int | None], _Flight] = {}
        self._pending = 0
        self.hits = self.misses = self.stores = self.coalesced = 0

    def clear_cache(self) -> None:
        self._cache.clear()
        self._bytes = 0

    async def _produce(self, key: str, request: TTSRequest) -> tuple[TTSResult, float]:
        async with self._slots:
            started = perf_counter()
            require_current_epoch()
            result = await self.provider.synthesize(request)
            require_current_epoch()
            if result.turn_epoch is not None and result.turn_epoch != request.turn_epoch:
                raise asyncio.CancelledError("Stale TTS result")
            # Cached PCM is reusable content, never an old turn delivery envelope.
            result = replace(result, turn_epoch=None)
            synthesis_ms = (perf_counter() - started) * 1000
            validate_request(replace(request, format=result.format), self.max_text_bytes)
            if result.format != request.format:
                raise ValueError("TTS returned an unexpected audio format")
            validate_pcm16(result.audio)
            if (
                not 0
                < len(result.audio)
                <= request.format.sample_rate_hz * self.max_audio_secs * 2
            ):
                raise ValueError("TTS audio is empty or exceeds its duration bound")
            duration = round(len(result.audio) / 2 / result.format.sample_rate_hz * 1000)
            result = replace(result, duration_ms=duration)
            if self.cache_entries and len(result.audio) <= self.cache_bytes:
                while self._cache and (
                    len(self._cache) >= self.cache_entries
                    or self._bytes + len(result.audio) > self.cache_bytes
                ):
                    _, old = self._cache.popitem(last=False)
                    self._bytes -= len(old.audio)
                self._cache[key] = result
                self._bytes += len(result.audio)
                self.stores += 1
            return result, synthesis_ms

    async def synthesize(
        self, request: TTSRequest, context: SafetyContext, *, cpu_worker=None
    ) -> SynthesizedSpeech:
        require_current_epoch()
        epoch = current_turn_epoch()
        if epoch is not None and request.turn_epoch is not None and request.turn_epoch != epoch:
            raise asyncio.CancelledError("Stale TTS request")
        validate_request(request, self.max_text_bytes)
        request = replace(request, metadata=dict(request.metadata),
                          turn_epoch=epoch if epoch is not None else request.turn_epoch)
        if self._pending >= self.max_pending:
            raise ValueError("TTS admission limit reached")
        self._pending += 1
        try:
            async with asyncio.timeout(self.timeout_secs):
                return await self._synthesize(request, context, cpu_worker=cpu_worker)
        finally:
            self._pending -= 1

    async def _synthesize(
        self, request: TTSRequest, context: SafetyContext, *, cpu_worker=None
    ) -> SynthesizedSpeech:
        started = perf_counter()
        # Check before spelling digits, so normalization cannot erase an amount signal.
        original = await self.safety.check(request.text, context)
        require_current_epoch()
        requested_language = language_code(request.language)
        normalized = (await cpu_worker.run(normalize_tts_text, original.text, request.language)
                      if cpu_worker else normalize_tts_text(original.text, request.language))
        require_current_epoch()
        if len(normalized.encode("utf-8")) > self.max_text_bytes:
            raise ValueError("Normalized TTS text exceeds its bounded UTF-8 size")
        final = await self.safety.check(normalized, context)
        require_current_epoch()
        canonical = final.text
        pronunciation = pronunciation_for(canonical)
        canonical_sha256 = pronunciation.canonical_sha256 if pronunciation else None
        spoken = final
        if pronunciation is not None:
            # Same safety/audit authority adjudicates the exact pronunciation before
            # any model identity/cache lookup. Caller language cannot change policy.
            spoken = await self.safety.check_pronunciation(pronunciation, context)
            require_current_epoch()
            # Audit failure uses the trusted hard-fail rendering, not the original line.
            if spoken.canonical_text is None:
                raise ValueError("Safety pronunciation did not retain its canonical binding")
            pronunciation = pronunciation_for(spoken.canonical_text)
        request = replace(
            request,
            text=spoken.text,
            language=pronunciation.language if pronunciation else requested_language,
            voice=request.voice or self.default_voice,
        )
        validate_request(request, self.max_text_bytes)
        safety_normalization_ms = (perf_counter() - started) * 1000
        identity_method = getattr(self.provider, "cache_identity", None)
        version = await identity_method(request) if identity_method else self.model_version
        require_current_epoch()
        if type(version) is not str or not version or len(version) > 256:
            raise ValueError("TTS provider returned an invalid model identity")
        key = hashlib.sha256(
            json.dumps(
                [
                    request.text,
                    request.language,
                    request.voice,
                    version,
                    NORMALIZATION_VERSION,
                    pronunciation.identity if pronunciation else None,
                    request.format.sample_rate_hz,
                    request.format.channels,
                    request.format.encoding,
                    sorted(request.metadata.items()),
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        status: Literal["hit", "miss", "coalesced"]
        if key in self._cache:
            self.hits += 1
            result = self._cache.pop(key)
            self._cache[key] = result
            status, synthesis_ms = "hit", 0.0
        else:
            flight_key = (key, request.turn_epoch)
            flight = self._flights.get(flight_key)
            if flight is None:
                self.misses += 1
                flight = _Flight(asyncio.create_task(self._produce(key, request)))
                self._flights[flight_key] = flight
                status = "miss"
            else:
                self.coalesced += 1
                status = "coalesced"
            flight.waiters += 1
            try:
                result, synthesis_ms = await asyncio.shield(flight.task)
            finally:
                flight.waiters -= 1
                if not flight.waiters:
                    self._flights.pop(flight_key, None)
                    if not flight.task.done():
                        flight.task.cancel()
                        await asyncio.gather(flight.task, return_exceptions=True)
        require_current_epoch()
        result = replace(result, turn_epoch=request.turn_epoch)
        if cpu_worker:
            frames = list(await cpu_worker.run(build_mulaw_frames, result.audio, result.format.sample_rate_hz))
        else:
            # Bound each CPU batch and yield between them so timeout/barge-in remains responsive.
            converter = PCMResampler(result.format.sample_rate_hz, 8000)
            framer = PCMFramer(8000)
            frames = []
            for offset in range(0, len(result.audio), 3200):
                pcm = converter.feed(result.audio[offset : offset + 3200])
                frames.extend(encode(frame) for frame in framer.feed(pcm))
                await asyncio.sleep(0)
            frames.extend(encode(frame) for frame in framer.feed(converter.finish()))
            if tail := framer.finish(pad=True):
                frames.append(encode(tail))
        require_current_epoch()
        elapsed = (perf_counter() - started) * 1000
        duration_ms = len(result.audio) / 2 / result.format.sample_rate_hz * 1000
        return SynthesizedSpeech(
            request.text,
            result,
            tuple(frames),
            status,
            synthesis_ms,
            elapsed,
            synthesis_ms / duration_ms,
            version,
            original.audit_ok and final.audit_ok and spoken.audit_ok,
            canonical,
            requested_language,
            request.language,
            request.voice or self.default_voice,
            pronunciation.version if pronunciation else None,
            canonical_sha256,
            pronunciation.canonical_text if pronunciation else canonical,
            pronunciation.spoken_sha256 if pronunciation else None,
            safety_normalization_ms,
        )
