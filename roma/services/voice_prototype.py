"""Section 20 application boundary. No carrier, audio SDK, or implicit provider switch."""

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from uuid import uuid4

from roma.core.logging import LogEvent, log_context, log_event, update_log_context
from roma.core.voice_errors import VoiceErrorCode, VoiceFailure, map_voice_error
from roma.domain.appointments.timeresolve import IST
from roma.domain.audio.turn_budget import TurnBudget
from roma.domain.safety.audit import SafetyContext
from roma.providers.ai.contracts import TTSRequest
from roma.providers.reliability import provider_budget
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
    def __init__(self, state, conversation, synthesis, speaker, store, *, clock=perf_counter, budget=None, streaming=None, cpu_worker=None):
        self.state, self.conversation, self.synthesis = state, conversation, synthesis
        self.speaker, self.store, self.clock = speaker, store, clock
        self.budget = budget or TurnBudget()
        self.correlation_id = uuid4().hex
        self.streaming, self.cpu_worker = streaming, cpu_worker
        self.queue_metrics = {}
        self.stream_fallbacks = 0
        self.attempt = 0
        self._busy = False
        self._started = clock()
        self._poisoned = False
        self._response_task = None
        self._delivery = None
        self._control_lock = asyncio.Lock()
        self._stop_reason = None
        self._speech_ms = 0
        self.disconnected = False
        self.release_on_disconnect = None
        self._filler = None

    async def respond(self, transcript, input_timings=None):
        remaining = 300 - (self.clock() - self._started)
        if remaining <= 0:
            raise RuntimeError("Voice session reached its five-minute limit")
        with provider_budget(remaining), log_context(call_id=self.state.call_sid, correlation_id=self.correlation_id,
                         request_id=uuid4().hex, turn_id=self.attempt + 1,
                         stage=self.state.stage.value,
                         language=self.state.language_router.current.value):
            async with asyncio.timeout(remaining):
                if self.streaming:
                    # A previous clear must finish before any new turn can speak.
                    async with self._control_lock:
                        pass
                return await self._respond(transcript, input_timings)

    async def _respond(self, transcript, input_timings=None):
        if self._busy or self._poisoned or self.disconnected:
            raise RuntimeError("Voice session is busy or requires restart")
        if self.clock() - self._started >= 300:
            raise RuntimeError("Voice session reached its five-minute limit")
        self._busy = True
        self._response_task = asyncio.current_task()
        self._stop_reason = None
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
        log_event(LogEvent.TURN_STARTED)
        log_event(LogEvent.ASR_COMPLETED, latency_ms=transcript.asr_ms,
                  error_code=map_voice_error(transcript.error, stage=VoiceErrorCode.ASR).value
                  if transcript.error or not transcript.text.strip() else None)
        stage = VoiceErrorCode.DB
        try:
            # Durable receipt before business mutation; no session spans any inference.
            await self.store.save(self.state.call_sid, record)
            if transcript.error or not transcript.text.strip():
                error = map_voice_error(transcript.error or "asr-empty", stage=VoiceErrorCode.ASR).value
                return VoiceTurnResult(
                    self.attempt, "failed", record["stage"], record["language"], timings, error
                )
            if self.streaming is not None:
                from roma.services.streaming_voice import StreamingDelivery

                self.queue_metrics = {}
                record["caller"] = transcript.text
                llm_before = getattr(self.conversation.provider, "elapsed_ms", 0.0)
                safety_before = getattr(self.conversation, "safety_elapsed_ms", 0.0)
                controller_before = getattr(self.conversation, "controller_elapsed_ms", 0.0)
                stage = VoiceErrorCode.LLM
                delivery = StreamingDelivery(self, record, timings)
                self._delivery = delivery
                async with delivery:
                    trace = await self.conversation.turn(
                        self.state, transcript.text, now=datetime.now(IST),
                        elapsed_secs=self.clock() - self._started,
                        on_sentence=delivery.enqueue, stream_config=self.streaming,
                        stream_metrics=self.queue_metrics, cpu_worker=self.cpu_worker,
                    )
                timings["llm_ms"] = max(0, getattr(self.conversation.provider, "elapsed_ms", 0.0) - llm_before)
                timings["safety_ms"] = timings.get("safety_ms", 0) + max(0, getattr(self.conversation, "safety_elapsed_ms", 0.0) - safety_before)
                if delivery.first_write is not None and transcript.speech_end_at is not None:
                    timings["speech_to_first_audio_ms"] = max(0, (delivery.first_write - transcript.speech_end_at) * 1000)
                error = delivery.error_code or (map_voice_error(trace.provider_error, stage=VoiceErrorCode.LLM).value if trace.provider_error else None)
                if error is None and self.state.booking_error == "BOOKING_UNAVAILABLE":
                    error = VoiceErrorCode.BOOKING_DATA.value
                timings["routing_controller_ms"] = max(0, getattr(self.conversation, "controller_elapsed_ms", 0.0) - controller_before)
                record.update(stage=self.state.stage.value, language=self.state.language_router.current.value)
                update_log_context(stage=record["stage"], language=record["language"])
                if timings["llm_ms"] > 0:
                    log_event(LogEvent.LLM_FAILED if error == VoiceErrorCode.LLM.value else LogEvent.LLM_COMPLETED,
                              latency_ms=timings["llm_ms"], error_code=error if error == VoiceErrorCode.LLM.value else None)
                log_event(LogEvent.CONTROLLER_COMPLETED, latency_ms=timings["routing_controller_ms"])
                log_event(LogEvent.SAFETY_COMPLETED, latency_ms=timings["safety_ms"],
                          error_code=error if error == VoiceErrorCode.SAFETY.value else None)
                status = "played"
                return VoiceTurnResult(self.attempt, status, record["stage"], record["language"], timings,
                                       error, self.budget.evaluate(timings))
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
            update_log_context(stage=record["stage"], language=record["language"])
            if timings["llm_ms"] > 0:
                llm_failed = trace.provider_error and map_voice_error(trace.provider_error) == VoiceErrorCode.LLM
                log_event(LogEvent.LLM_FAILED if llm_failed else LogEvent.LLM_COMPLETED,
                          latency_ms=timings["llm_ms"],
                          error_code=VoiceErrorCode.LLM.value if llm_failed else None)
            log_event(LogEvent.CONTROLLER_COMPLETED, latency_ms=timings["routing_controller_ms"])
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
            log_event(LogEvent.SAFETY_COMPLETED, latency_ms=timings["safety_ms"],
                      error_code=None if speech.audit_ok else VoiceErrorCode.SAFETY.value)
            log_event(LogEvent.TTS_COMPLETED, latency_ms=timings["tts_ms"])
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
            log_event(LogEvent.PLAYBACK_COMPLETED, latency_ms=timings["playback_ms"])
            status = "played"
            return VoiceTurnResult(
                self.attempt, status, record["stage"], record["language"], timings, error, self.budget.evaluate(timings)
            )
        except asyncio.CancelledError:
            error = map_voice_error(asyncio.CancelledError()).value
            self._poisoned = not (self.streaming and self._stop_reason == "interrupted")
            if self.streaming and self._stop_reason:
                status = self._stop_reason
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
                status, error = "failed", VoiceErrorCode.DB.value
                raise VoiceFailure(VoiceErrorCode.DB) from None
            finally:
                log_event(LogEvent.TURN_COMPLETED if status == "played" else LogEvent.TURN_FAILED,
                          latency_ms=timings["response_total_ms"], error_code=error, status=status)
                for name, snapshot in self.queue_metrics.items():
                    log_event(LogEvent.QUEUE_SUMMARY, queue_name=name, queue_metrics=snapshot)
                if status in {"interrupted", "disconnected"}:
                    log_event(LogEvent.TURN_INTERRUPTED if status == "interrupted" else LogEvent.CALL_DISCONNECTED,
                              error_code=error, status=status)
                self._response_task = None
                self._delivery = None
                self._busy = False

    def barge_in_due(self, probability, duration_ms):
        if not self.streaming:
            return False
        if not math.isfinite(probability) or not 0 <= probability <= 1 or not math.isfinite(duration_ms) or not 0 < duration_ms <= 2000:
            raise ValueError("Invalid speech evidence")
        if not self._busy or probability < self.streaming.speech_threshold:
            self._speech_ms = 0
            return False
        self._speech_ms += duration_ms
        if self._speech_ms < self.streaming.minimum_speech_ms:
            return False
        self._speech_ms = 0
        return True

    async def observe_speech(self, probability, duration_ms):
        return await self.interrupt() if self.barge_in_due(probability, duration_ms) else False

    async def interrupt(self, *, owner=None):
        return await self._cancel_response(disconnect=False, owner=owner)

    async def disconnect(self):
        return await self._cancel_response(disconnect=True)

    async def _cancel_response(self, *, disconnect, owner=None):
        if not self.streaming:
            raise RuntimeError("Streaming control requires explicit streaming mode")
        async with self._control_lock:
            if self.disconnected:
                return False
            target = self._response_task
            if owner is not None and target is not owner:
                return False
            if not disconnect and (target is None or target.done()):
                return False
            self._stop_reason = "disconnected" if disconnect else "interrupted"
            self.disconnected = disconnect
            if self._delivery:
                self._delivery.active = False
            cleanup_error = None
            if target is not None and target is not asyncio.current_task() and not target.done():
                target.cancel()
                done, pending = await asyncio.wait({target}, timeout=self.streaming.cleanup_timeout_secs)
                if pending:
                    self.disconnected = True
                    self._poisoned = True
                    target.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                    close = getattr(self.speaker, "close", None)
                    if close:
                        try:
                            async with asyncio.timeout(self.streaming.cleanup_timeout_secs):
                                await close()
                        except Exception:  # noqa: BLE001 -- still clear/release/persist below.
                            cleanup_error = VoiceErrorCode.DEVICE
                for task in done:
                    if not task.cancelled():
                        task.exception()
            clear = getattr(self.speaker, "clear", None)
            try:
                if clear:
                    async with asyncio.timeout(self.streaming.cleanup_timeout_secs):
                        await clear()
            except Exception:  # noqa: BLE001 -- clear failure must still release the call.
                cleanup_error = VoiceErrorCode.DEVICE
                self.disconnected = self._poisoned = True
                close = getattr(self.speaker, "close", None)
                if close:
                    try:
                        async with asyncio.timeout(self.streaming.cleanup_timeout_secs):
                            await close()
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                if self.disconnected:
                    self._poisoned = True
                    try:
                        if self.release_on_disconnect:
                            async with asyncio.timeout(self.streaming.cleanup_timeout_secs):
                                await self.release_on_disconnect()
                    except Exception:  # noqa: BLE001
                        cleanup_error = VoiceErrorCode.DB
                    finally:
                        finish = getattr(self.store, "finish", None)
                        if finish:
                            try:
                                await finish(self.state.call_sid, failed=True, disconnected=True)
                            except Exception:  # noqa: BLE001
                                cleanup_error = VoiceErrorCode.DB
            if cleanup_error:
                raise VoiceFailure(cleanup_error) from None
            return True

    async def prime_filler(self):
        if not self.streaming:
            return
        speech = await self.synthesis.synthesize(
            TTSRequest("अच्छा।", language="hi"),
            SafetyContext(self.state.call_sid, max(1, self.state.turn_count), self.state.stage.value),
        )
        if speech.audit_ok and speech.spoken_canonical_text == "अच्छा।":
            self._filler = speech.result

    async def play_cached_filler(self):
        if self._filler is None or self._busy or self.disconnected:
            return False
        check = await self.synthesis.safety.check(
            "अच्छा।", SafetyContext(self.state.call_sid, max(1, self.state.turn_count), self.state.stage.value)
        )
        if not check.audit_ok or check.text != "अच्छा।":
            return False
        async with asyncio.timeout(self.streaming.playback_timeout_secs):
            await self.speaker.play(self._filler)
        return True


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
