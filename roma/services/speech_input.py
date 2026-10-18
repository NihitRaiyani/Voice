"""Single-call, bounded 16 kHz speech-input lab; live telephony integration is gated.

20 ms transport blocks are reframed into Silero's 32 ms frames. ASR sees only
completed utterances. Feed results to the existing accepted-turn controller so
language evidence is saved exactly once alongside conversation state.
"""

from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from roma.domain.audio.endpointing import FRAME_BYTES, EndpointDetector, Utterance
from roma.domain.audio.pcm import AudioFormatError, validate_pcm16
from roma.domain.conversation.language import Language
from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import (
    AudioFormat,
    ProviderUnavailable,
    STTProvider,
    STTRequest,
)


class SpeechProbability(Protocol):
    def probability(self, pcm: bytes) -> float: ...
    def reset(self) -> None: ...


@dataclass(frozen=True)
class SpeechTranscript:
    text: str
    language: str | None
    duration_ms: float
    reason: str
    asr_ms: float
    error: str | None = None


def language_hint(state: CallState) -> str:
    route = state.language_router.current
    if route in (Language.GUJARATI, Language.GUJARATI_ENGLISH):
        return "gu"
    if route == Language.ENGLISH:
        return "en"
    return "hi"


class SpeechInputService:
    """Sequential lab adapter, with one bounded audio batch and no hidden task queue.

    A service belongs to one call. Concurrent feeds/reset are rejected; cancellation
    discards partial endpoint state. No silence timer runs while ASR is executing.
    Input sample duration, not network delay, drives endpointing. Explicit gaps flush
    the old utterance, discard a partial frame and reset Silero recurrent state.
    """

    def __init__(
        self,
        provider: STTProvider,
        scorer: SpeechProbability,
        state: CallState,
        *,
        endpoint: EndpointDetector | None = None,
    ) -> None:
        self.provider, self.scorer, self.call_state = provider, scorer, state
        self.endpoint = endpoint or EndpointDetector()
        self._carry = bytearray()
        self._busy = False

    @property
    def pending_bytes(self) -> int:
        return len(self._carry)

    async def _transcribe(
        self, segments: tuple[Utterance, ...]
    ) -> tuple[SpeechTranscript, ...]:
        results = []
        for segment in segments:
            started = perf_counter()
            try:
                result = await self.provider.transcribe(
                    STTRequest(
                        segment.pcm,
                        AudioFormat(16000),
                        language_hint(self.call_state),
                    )
                )
                results.append(
                    SpeechTranscript(
                        result.text,
                        result.language,
                        segment.duration_ms,
                        segment.reason,
                        (perf_counter() - started) * 1000,
                    )
                )
            except ValueError:
                # A saved English route is unsupported by IndicConformer. Report it;
                # do not silently relabel English or switch to an unapproved model.
                results.append(
                    SpeechTranscript(
                        "",
                        None,
                        segment.duration_ms,
                        segment.reason,
                        (perf_counter() - started) * 1000,
                        "stt-unsupported-or-invalid-request",
                    )
                )
            except ProviderUnavailable:
                # Provider details may contain credentials or transcript text; expose only a code.
                results.append(
                    SpeechTranscript(
                        "",
                        None,
                        segment.duration_ms,
                        segment.reason,
                        (perf_counter() - started) * 1000,
                        "stt-unavailable",
                    )
                )
        return tuple(results)

    def _enter(self) -> None:
        if self._busy:
            raise RuntimeError("One speech-input operation may run per call")
        self._busy = True

    async def feed(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        discontinuity: bool = False,
    ) -> tuple[SpeechTranscript, ...]:
        if (
            not isinstance(pcm, bytes)
            or type(sample_rate) is not int
            or sample_rate != 16000
            or type(channels) is not int
            or channels != 1
            or len(pcm) > 32000
            or type(discontinuity) is not bool
        ):
            raise AudioFormatError("Expected at most one second of 16 kHz mono PCM16 per batch")
        validate_pcm16(pcm)
        self._enter()
        try:
            segments: list[Utterance] = []
            if discontinuity:
                segments.extend(self.endpoint.discontinuity())
                self._carry.clear()
                self.scorer.reset()
            self._carry.extend(pcm)
            while len(self._carry) >= FRAME_BYTES:
                frame = bytes(self._carry[:FRAME_BYTES])
                del self._carry[:FRAME_BYTES]
                segments.extend(self.endpoint.feed(frame, self.scorer.probability(frame)))
            return await self._transcribe(tuple(segments))
        except BaseException:
            self._discard()
            raise
        finally:
            self._busy = False

    async def finish(self, *, pad_tail: bool = False) -> tuple[SpeechTranscript, ...]:
        if self._carry and not pad_tail:
            raise AudioFormatError("Partial Silero frame: explicitly pad_tail or reset")
        self._enter()
        try:
            segments: list[Utterance] = []
            if self._carry:
                frame = bytes(self._carry).ljust(FRAME_BYTES, b"\0")
                self._carry.clear()
                segments.extend(self.endpoint.feed(frame, self.scorer.probability(frame)))
            segments.extend(self.endpoint.flush())
            return await self._transcribe(tuple(segments))
        except BaseException:
            self._discard()
            raise
        finally:
            self._busy = False

    def _discard(self) -> None:
        self._carry.clear()
        self.endpoint.reset()
        self.scorer.reset()

    def reset(self) -> None:
        if self._busy:
            raise RuntimeError("Cannot reset speech input during an operation")
        self._discard()


__all__ = ["SpeechInputService", "SpeechProbability", "SpeechTranscript", "language_hint"]
