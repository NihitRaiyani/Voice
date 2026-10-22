"""Section 20 application boundary. No carrier, audio SDK, or implicit provider switch."""

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

from roma.core.voice_errors import VoiceErrorCode, VoiceFailure, map_voice_error
from roma.domain.appointments.timeresolve import IST
from roma.domain.audio.turn_budget import TurnBudget
from roma.domain.safety.audit import SafetyContext
from roma.providers.ai.contracts import TTSRequest
from roma.services.voice_pronunciation import render_voice_line


@dataclass(frozen=True)
class VoiceTurnResult:
    attempt: int
    status: str
    stage: str
    language: str
    timings: dict[str, float]
    error_code: str | None
    budget: dict | None = None


class VoicePrototype:
    def __init__(self, state, conversation, synthesis, speaker, store, *, clock=perf_counter, budget=None):
        self.state, self.conversation, self.synthesis = state, conversation, synthesis
        self.speaker, self.store, self.clock = speaker, store, clock
        self.budget = budget or TurnBudget()
        self.attempt = 0
        self._busy = False
        self._started = clock()
        self._poisoned = False

    async def respond(self, transcript, input_timings=None):
        remaining = 300 - (self.clock() - self._started)
        if remaining <= 0:
            raise RuntimeError("Voice session reached its five-minute limit")
        async with asyncio.timeout(remaining):
            return await self._respond(transcript, input_timings)

    async def _respond(self, transcript, input_timings=None):
        if self._busy or self._poisoned:
            raise RuntimeError("Voice session is busy or requires restart")
        if self.clock() - self._started >= 300:
            raise RuntimeError("Voice session reached its five-minute limit")
        self._busy = True
        self.attempt += 1
        timings = dict(input_timings or {})
        if not set(timings) <= {"capture_ms", "vad_endpoint_ms", "endpoint_wait_ms"} or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for v in timings.values()
        ):
            self._busy = False
            raise ValueError("Invalid input timing")
        timings["asr_ms"] = transcript.asr_ms
        if not math.isfinite(transcript.asr_ms) or transcript.asr_ms < 0:
            self._busy = False
            raise ValueError("Invalid ASR timing")
        started = self.clock()
        status, error, trace = "failed", None, None
        record = {
            "attempt": self.attempt,
            "turn_number": None,
            "stage": self.state.stage.value,
            "language": self.state.language_router.current.value,
            "caller": None,
            "agent": None,
            "timings": timings,
            "status": "received",
            "error_code": None,
        }
        stage = VoiceErrorCode.DB
        try:
            # Durable receipt before business mutation; no session spans any inference.
            await self.store.save(self.state.call_sid, record)
            if transcript.error or not transcript.text.strip():
                error = map_voice_error(transcript.error or "asr-empty", stage=VoiceErrorCode.ASR).value
                return VoiceTurnResult(
                    self.attempt, "failed", record["stage"], record["language"], timings, error
                )
            tick = self.clock()
            safety_before = getattr(self.conversation, "safety_elapsed_ms", 0.0)
            before_turn = self.state.turn_count
            llm_before = getattr(self.conversation.provider, "elapsed_ms", 0.0)
            stage = VoiceErrorCode.LLM
            trace = await self.conversation.turn(
                self.state,
                transcript.text,
                now=datetime.now(IST),
                elapsed_secs=self.clock() - self._started,
            )
            timings["llm_ms"] = max(
                0.0, getattr(self.conversation.provider, "elapsed_ms", 0.0) - llm_before
            )
            controller_safety_ms = max(0.0, getattr(self.conversation, "safety_elapsed_ms", 0.0) - safety_before)
            timings["routing_controller_ms"] = max(
                0.0, (self.clock() - tick) * 1000 - timings["llm_ms"] - controller_safety_ms
            )
            if self.state.turn_count > before_turn:
                record["turn_number"] = self.state.turn_count
            record.update(
                stage=self.state.stage.value,
                language=self.state.language_router.current.value,
                caller=transcript.text,
                agent=trace.final_response,
            )
            stage = VoiceErrorCode.TTS
            spoken = render_voice_line(trace.final_response, self.state)
            tick = self.clock()
            speech = await self.synthesis.synthesize(
                TTSRequest(spoken, language="hi"),
                SafetyContext(
                    self.state.call_sid, max(1, self.state.turn_count), self.state.stage.value
                ),
            )
            timings["safety_tts_cache_ms"] = (self.clock() - tick) * 1000
            timings["safety_ms"] = controller_safety_ms + speech.safety_normalization_ms
            timings["tts_ms"] = max(0.0, timings["safety_tts_cache_ms"] - speech.safety_normalization_ms)
            timings["tts_inference_ms"] = speech.synthesis_ms
            timings["audio_ms"] = speech.result.duration_ms or 0
            timings["safety_normalization_ms"] = speech.safety_normalization_ms
            timings["tts_cache_frames_ms"] = max(
                0.0, speech.elapsed_ms - speech.safety_normalization_ms
            )
            raw_error = trace.provider_error if speech.audit_ok else "safety-audit-unavailable"
            if raw_error is None and self.state.booking_error == "BOOKING_UNAVAILABLE":
                raw_error = "BOOKING_UNAVAILABLE"
            error = map_voice_error(raw_error, stage=VoiceErrorCode.LLM).value if raw_error else None
            record.update(status="ready", error_code=error, agent=speech.spoken_canonical_text)
            stage = VoiceErrorCode.DB
            await self.store.save(self.state.call_sid, record)
            tick = self.clock()
            stage = VoiceErrorCode.DEVICE
            await self.speaker.play(speech.result)
            first_write = getattr(self.speaker, "first_write_at", None)
            if first_write is not None:
                timings["playback_start_ms"] = max(0.0, (first_write - tick) * 1000)
                if transcript.speech_end_at is not None:
                    timings["speech_to_first_audio_ms"] = max(0.0, (first_write - transcript.speech_end_at) * 1000)
            timings["playback_ms"] = (self.clock() - tick) * 1000
            status = "played"
            return VoiceTurnResult(
                self.attempt, status, record["stage"], record["language"], timings, error, self.budget.evaluate(timings)
            )
        except asyncio.CancelledError:
            error = map_voice_error(asyncio.CancelledError()).value
            self._poisoned = True
            raise
        except Exception as exc:  # noqa: BLE001 -- fixed codes only.
            code = map_voice_error(exc, stage=stage)
            error = code.value
            self._poisoned = True
            raise VoiceFailure(code) from None
        finally:
            timings["response_total_ms"] = max(0.0, (self.clock() - started) * 1000)
            record.update(status=status, error_code=error, timings=dict(timings))
            try:
                await self.store.save(self.state.call_sid, record)
            except Exception:  # noqa: BLE001 -- database failure must stop this session.
                self._poisoned = True
                raise VoiceFailure(VoiceErrorCode.DB) from None
            finally:
                self._busy = False


class InMemoryVoiceTurnStore:
    """Synthetic tests only. Does not claim durable evidence."""

    def __init__(self):
        self.records = []

    async def save(self, sid, record):
        import copy

        if len(self.records) >= 1000:
            raise ValueError("Synthetic voice store capacity reached")
        self.records.append((sid, copy.deepcopy(record)))


class TimedLLMProvider:
    """Per-call stage wall time, including extraction and SDK fallback/error paths."""

    def __init__(self, provider):
        self.provider = provider
        self.elapsed_ms = 0.0

    async def generate(self, request):
        tick = perf_counter()
        stream = self.provider.generate(request)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            try:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
            finally:
                self.elapsed_ms += (perf_counter() - tick) * 1000
