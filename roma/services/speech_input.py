"""Single-call, bounded 16 kHz speech-input lab; live telephony integration is gated.

20 ms transport blocks are reframed into Silero's 32 ms frames. ASR sees only
bounded audio chunks. Maximum-size chunks stay internal until silence or an
explicit stream boundary finalizes one bounded caller transcript. Feed these
final results to the accepted-turn controller to save language evidence once.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Literal, Protocol

from roma.core.turn_epoch import TurnEpoch
from roma.domain.audio.endpointing import (
    FRAME_BYTES,
    EndpointConfig,
    EndpointDetector,
    Utterance,
)
from roma.domain.audio.pcm import AudioFormatError, validate_pcm16
from roma.domain.audio.turn_policy import TurnPolicy
from roma.domain.conversation.language import Language
from roma.domain.conversation.state import CallState
from roma.domain.safety.inputs import CALLER_TEXT_LIMIT
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
    last_speech_sample: int | None = None
    speech_end_at: float | None = None
    turn_epoch: int | None = None


def language_hint(state: CallState) -> str:
    route = state.language_router.current
    if route == Language.GUJARATI:
        return "gu"
    if route == Language.GUJARATI_ENGLISH:
        return "gu-en"
    if route == Language.ENGLISH:
        return "en"
    return "hi-en" if route == Language.HINDI_ENGLISH else "hi"


class SpeechInputService:
    """Sequential lab adapter, with one bounded audio batch and no hidden task queue.

    A service belongs to one call. Concurrent feeds/reset are rejected; cancellation
    discards partial endpoint state. No silence timer runs while ASR is executing.
    Input sample duration, not network delay, drives endpointing. Explicit gaps flush
    the old utterance, discard a partial frame and reset Silero recurrent state.
    An explicit prototype policy keeps the provisional endpoint open through its
    continuation grace; resumed speech resets the same audio-duration timer.
    Legacy standalone labs retain their explicitly configured endpoint behavior.
    Only finalized turns are returned. Internal ASR chunk text is capped at the
    caller-text limit; overflow or any chunk error rejects the entire turn.
    """

    def __init__(
        self,
        provider: STTProvider,
        scorer: SpeechProbability,
        state: CallState,
        *,
        endpoint: EndpointDetector | None = None,
        policy: TurnPolicy | None = None,
        stt_metadata: Mapping[str, str] | None = None,
        epoch_guard: TurnEpoch | None = None,
    ) -> None:
        self.provider, self.scorer, self.call_state = provider, scorer, state
        self.epochs = epoch_guard
        self._capture_epoch: int | None = None
        self.on_utterance_finalizing = None
        if endpoint is not None and policy is not None:
            raise ValueError("Supply a turn policy or an explicit legacy endpoint, not both")
        self.policy = policy
        config = EndpointConfig(silence_ms=policy.endpoint_wait_ms,
                                max_utterance_ms=policy.max_utterance_ms) if policy else None
        self.endpoint = endpoint or EndpointDetector(config)
        self._stt_metadata = dict(stt_metadata or {})
        self._carry = bytearray()
        self._source_end = 0
        self._busy = False
        self._clear_turn()

    def _clear_turn(self) -> None:
        self._turn_parts: list[str] = []
        self._turn_chars = 0
        self._turn_language: str | None = None
        self._turn_duration_ms = 0.0
        self._turn_asr_ms = 0.0
        self._turn_error: str | None = None
        self._turn_last_speech: int | None = None

    def _turn_failure(self, code: str) -> None:
        self._turn_error = self._turn_error or code
        self._turn_parts.clear()
        self._turn_chars = 0
        self._turn_language = None

    @property
    def pending_bytes(self) -> int:
        return len(self._carry)

    def segment_decoder(self, *, epoch_guard: TurnEpoch) -> "SpeechInputService":
        """Create independent decode state for externally endpointed capture.

        The decoder shares the configured provider and language state; it never
        feeds or resets the live scorer/endpoint.
        """
        return SpeechInputService(self.provider, self.scorer, self.call_state,
                                  policy=self.policy, stt_metadata=self._stt_metadata,
                                  epoch_guard=epoch_guard)

    async def transcribe_segment(self, segment: Utterance, *, turn_epoch: int) -> SpeechTranscript:
        """Decode one already-final, bounded segment without touching capture."""
        if not isinstance(segment, Utterance) or segment.reason == "max_duration":
            raise ValueError("Expected a finalized utterance")
        validate_pcm16(segment.pcm)
        if (not segment.pcm or len(segment.pcm) > 960000
                or segment.end_sample - segment.start_sample != len(segment.pcm) // 2):
            raise AudioFormatError("Expected at most thirty seconds of original PCM")
        self._enter()
        try:
            self._clear_turn()
            self._capture_epoch = turn_epoch
            self._source_end = segment.end_sample
            return (await self._transcribe((segment,)))[0]
        finally:
            self._clear_turn()
            self._capture_epoch = None
            self._busy = False

    async def _transcribe(
        self, segments: tuple[Utterance, ...]
    ) -> tuple[SpeechTranscript, ...]:
        results = []
        epoch = self._capture_epoch
        for segment in segments:
            if self.epochs is not None:
                self.epochs.require_current(epoch)
            if segment.reason != "max_duration" and self.on_utterance_finalizing is not None:
                self.on_utterance_finalizing()
            if self.policy is not None and segment.end_sample > self._source_end:
                # Silero needs a complete frame; ASR receives only original source samples.
                keep = max(0, self._source_end - segment.start_sample)
                segment = replace(segment, pcm=segment.pcm[:keep * 2],
                                  end_sample=segment.start_sample + keep)
            if segment.last_speech_sample is not None:
                last = min(segment.last_speech_sample, self._source_end)
                self._turn_last_speech = max(self._turn_last_speech or 0, last)
            # An empty boundary closes an exact maximum split without inventing
            # another ASR request or accepting its earlier chunks as turns.
            if segment.pcm:
                started = perf_counter()
                try:
                    result = await self.provider.transcribe(
                        STTRequest(
                            segment.pcm,
                            AudioFormat(16000),
                            language_hint(self.call_state),
                            self._stt_metadata,
                            turn_epoch=epoch,
                        )
                    )
                    if self.epochs is not None:
                        self.epochs.require_current(epoch)
                        if result.turn_epoch is not None and result.turn_epoch != epoch:
                            import asyncio
                            raise asyncio.CancelledError("Stale ASR result")
                    if result.error_code:
                        self._turn_failure("ASR-BATCH-REJECTED" if result.error_code == "ASR-BATCH-REJECTED" else "stt-unavailable")
                    if self._turn_error is None:
                        text = result.text.strip()
                        added_chars = len(text) + bool(text and self._turn_parts)
                        if self._turn_chars + added_chars > CALLER_TEXT_LIMIT:
                            self._turn_failure("stt-turn-too-long")
                        elif text:
                            self._turn_parts.append(text)
                            self._turn_chars += added_chars
                            self._turn_language = result.language
                except ValueError:
                    # Preserve the unsupported-route error without silently
                    # relabeling English or accepting an incomplete turn.
                    self._turn_failure("stt-unsupported-or-invalid-request")
                except ProviderUnavailable:
                    # Provider details may contain credentials or caller text.
                    self._turn_failure("stt-unavailable")
                self._turn_asr_ms += (perf_counter() - started) * 1000
                self._turn_duration_ms += segment.duration_ms
            if segment.reason != "max_duration":
                results.append(
                    SpeechTranscript(
                        " ".join(self._turn_parts),
                        self._turn_language,
                        self._turn_duration_ms,
                        segment.reason,
                        self._turn_asr_ms,
                        self._turn_error,
                        self._turn_last_speech,
                        turn_epoch=epoch,
                    )
                )
                self._clear_turn()
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
        turn_epoch: int | None = None,
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
        if self.epochs is not None:
            epoch = self.epochs.snapshot() if turn_epoch is None else turn_epoch
            self.epochs.require_current(epoch)
            if self._capture_epoch is not None and self._capture_epoch != epoch:
                self._discard()
            self._capture_epoch = epoch
        self._enter()
        try:
            segments: list[Utterance] = []
            if discontinuity:
                segments.extend(self.endpoint.discontinuity())
                self._carry.clear()
                self.scorer.reset()
            self._source_end = self.endpoint.position_samples + (len(self._carry) + len(pcm)) // 2
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

    async def finish(self, *, pad_tail: bool = False, endpoint_reason: Literal["flush", "deadline"] = "flush", turn_epoch: int | None = None) -> tuple[SpeechTranscript, ...]:
        if self.epochs is not None:
            epoch = self.epochs.snapshot() if turn_epoch is None else turn_epoch
            self.epochs.require_current(epoch)
            if self._capture_epoch is None:
                self._capture_epoch = epoch
            self.epochs.require_current(self._capture_epoch)
        if self._carry and not pad_tail:
            raise AudioFormatError("Partial Silero frame: explicitly pad_tail or reset")
        self._enter()
        try:
            segments: list[Utterance] = []
            if self._carry:
                frame = bytes(self._carry).ljust(FRAME_BYTES, b"\0")
                self._carry.clear()
                segments.extend(self.endpoint.feed(frame, self.scorer.probability(frame)))
            segments.extend(self.endpoint.flush(reason=endpoint_reason))
            return await self._transcribe(tuple(segments))
        except BaseException:
            self._discard()
            raise
        finally:
            self._busy = False

    def _discard(self) -> None:
        self._carry.clear()
        self._source_end = 0
        self._capture_epoch = None
        self._clear_turn()
        self.endpoint.reset()
        self.scorer.reset()

    def reset(self) -> None:
        if self._busy:
            raise RuntimeError("Cannot reset speech input during an operation")
        self._discard()


__all__ = ["SpeechInputService", "SpeechProbability", "SpeechTranscript", "language_hint"]
