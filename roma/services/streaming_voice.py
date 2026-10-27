"""Opt-in sentence delivery; bounded queues share one call's existing controller/stores."""

import asyncio
from dataclasses import replace
from time import perf_counter

from starlette.websockets import WebSocketDisconnect

from roma.core.async_queue import BoundedChannel, QueuePolicy, QueuePressure
from roma.core.logging import LogEvent, log_context, log_event, update_log_context
from roma.core.turn_epoch import current_turn_epoch
from roma.core.voice_errors import VoiceErrorCode, VoiceFailure, map_voice_error
from roma.domain.safety.audit import SafetyContext
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.ai.contracts import TTSRequest
from roma.services.voice_pronunciation import render_voice_line


class StreamingDelivery:
    def __init__(self, voice, record, timings):
        self.voice, self.record, self.timings = voice, record, timings
        self.epoch = current_turn_epoch()
        self.policy = voice.streaming
        self.text = self.policy.channel("tts_text")
        self.audio = self.policy.channel("tts_audio")
        self.active = False
        self.parts = []
        self.first_write = None
        self.error_code = None
        self.started = perf_counter()
        self.group = asyncio.TaskGroup()

    async def __aenter__(self):
        self.owner = asyncio.current_task()
        self.active = True
        await self.group.__aenter__()
        self.group.create_task(self._synthesize())
        self.group.create_task(self._play())
        return self

    def _owns_response(self):
        return (self.voice._delivery is self and not self.voice.disconnected
                and self.voice._response_task is self.owner and self.voice._stop_reason is None
                and self.voice.epochs.is_current(self.epoch))

    async def enqueue(self, line):
        if not self.active or not self._owns_response():
            raise asyncio.CancelledError
        if not await self.text.put((self.epoch, line)):
            if not self.active or not self._owns_response():
                raise asyncio.CancelledError
            raise QueuePressure()

    async def _synthesize(self):
        try:
            async for epoch, line in self.text:
                self.voice.epochs.require_current(epoch)
                if not self._owns_response():
                    raise asyncio.CancelledError
                state = self.voice.state
                update_log_context(stage=state.stage.value, language=state.language_router.current.value)
                started = perf_counter()
                try:
                    kwargs = {"cpu_worker": self.voice.cpu_worker} if self.voice.cpu_worker else {}
                    speech = await self.voice.synthesis.synthesize(
                        TTSRequest(render_voice_line(line, state), language="hi", turn_epoch=epoch),
                        SafetyContext(state.call_sid, max(1, state.turn_count), state.stage.value), **kwargs,
                    )
                except Exception:  # noqa: BLE001 -- stage code only.
                    raise VoiceFailure(VoiceErrorCode.TTS) from None
                if not self._owns_response():
                    self.audio.metrics.discarded += 1
                    raise asyncio.CancelledError
                if speech.result.turn_epoch is not None:
                    self.voice.epochs.require_current(speech.result.turn_epoch)
                speech = replace(speech, result=replace(speech.result, turn_epoch=epoch))
                if not speech.audit_ok:
                    self.error_code = VoiceErrorCode.SAFETY.value
                self.timings["tts_ms"] = self.timings.get("tts_ms", 0) + (perf_counter() - started) * 1000
                self.timings["safety_ms"] = self.timings.get("safety_ms", 0) + speech.safety_normalization_ms
                self.timings["tts_inference_ms"] = self.timings.get("tts_inference_ms", 0) + speech.synthesis_ms
                log_event(LogEvent.TTS_COMPLETED, latency_ms=(perf_counter() - started) * 1000)
                if not self.active or not self._owns_response() or (self.voice._response_task and self.voice._response_task.cancelling()):
                    self.audio.metrics.discarded += 1
                    continue
                if not await self.audio.put(speech):
                    raise QueuePressure()
        finally:
            await self.audio.close()

    async def _play(self):
        async for speech in self.audio:
            if not self.active or not self._owns_response() or (self.voice._response_task and self.voice._response_task.cancelling()):
                self.audio.metrics.discarded += 1
                continue
            self.voice.epochs.require_current(speech.result.turn_epoch)
            state = self.voice.state
            update_log_context(stage=state.stage.value, language=state.language_router.current.value)
            self.parts.append(speech.spoken_canonical_text)
            self.record.update(status="ready", stage=state.stage.value,
                               language=state.language_router.current.value,
                               turn_number=state.turn_count or None, agent=" ".join(self.parts))
            # Persist each ready, safe phrase before its first audible write.
            try:
                await self.voice.store.save(state.call_sid, self.record)
            except Exception:  # noqa: BLE001
                raise VoiceFailure(VoiceErrorCode.DB) from None
            if not self.active or not self._owns_response():
                self.audio.metrics.discarded += 1
                continue
            started = perf_counter()
            try:
                async with asyncio.timeout(self.policy.playback_timeout_secs):
                    await self.voice.speaker.play(speech.result)
            except Exception:  # noqa: BLE001
                raise VoiceFailure(VoiceErrorCode.DEVICE) from None
            if self.first_write is None:
                self.first_write = getattr(self.voice.speaker, "first_write_at", None)
            self.timings["playback_ms"] = self.timings.get("playback_ms", 0) + (perf_counter() - started) * 1000
            self.timings["audio_ms"] = self.timings.get("audio_ms", 0) + (speech.result.duration_ms or 0)
            log_event(LogEvent.PLAYBACK_COMPLETED, latency_ms=(perf_counter() - started) * 1000)

    async def _fallback(self):
        """Overflow/error: stop queued output, clear, then approved fallback or close."""
        if not self._owns_response():
            return
        self.voice.stream_fallbacks += 1
        try:
            async with asyncio.timeout(min(2, self.policy.playback_timeout_secs)):
                clear = getattr(self.voice.speaker, "clear", None)
                if clear:
                    await clear()
                if not self._owns_response():
                    return
                state = self.voice.state
                speech = await self.voice.synthesis.synthesize(
                    TTSRequest(render_voice_line(HARD_FAIL_LINE, state), language="hi", turn_epoch=self.epoch),
                    SafetyContext(state.call_sid, max(1, state.turn_count), state.stage.value),
                )
                if not self._owns_response():
                    return
                speech = replace(speech, result=replace(speech.result, turn_epoch=self.epoch))
                self.record.update(status="ready", agent=speech.spoken_canonical_text)
                await self.voice.store.save(state.call_sid, self.record)
                if not self._owns_response():
                    return
                await self.voice.speaker.play(speech.result)
        except Exception:  # noqa: BLE001 -- a stalled client cannot receive a spoken fallback.
            close = getattr(self.voice.speaker, "close", None)
            if close:
                try:
                    async with asyncio.timeout(self.policy.cleanup_timeout_secs):
                        await close()
                except Exception:  # noqa: BLE001
                    pass

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.active = False  # before cancellation joins: late results are not deliverable.
        await self.text.close(discard=exc_type is not None)
        if exc_type is not None:
            await self.audio.close(discard=True)
        try:
            return await self.group.__aexit__(exc_type, exc, tb)
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                log_event(LogEvent.STREAM_FALLBACK, error_code=map_voice_error(error, stage=VoiceErrorCode.TTS).value)
                await self._fallback()
            raise
        finally:
            self.active = False
            for channel in (self.text, self.audio):
                await channel.close(discard=True)
                self.voice.queue_metrics[channel.name] = channel.snapshot()
            with log_context(call_id=self.voice.state.call_sid, correlation_id=self.voice.correlation_id,
                             stage=self.voice.state.stage.value, language=self.voice.state.language_router.current.value):
                log_event(LogEvent.QUEUE_SUMMARY, queue_name=channel.name, queue_metrics=channel.snapshot())


class StreamingInput:
    """Unstable partials never enter the controller. Finals retain order or fail loudly.

    Audio is disposable only before endpoint/ASR acceptance. Any capture loss aborts
    this input turn rather than stitching a misleading final transcript across a gap.
    """
    def __init__(self, voice, *, capacity=8):
        self.voice = voice
        self.partials = BoundedChannel("partials", capacity=capacity, high=capacity, low=0,
                                       policy=QueuePolicy.DROP_OLDEST)
        self.audio = BoundedChannel("audio_in", capacity=capacity, high=capacity, low=0,
                                    policy=QueuePolicy.DROP_OLDEST)
        self.finals = BoundedChannel("finals", capacity=capacity, high=capacity, low=0,
                                     wait_timeout=voice.streaming.queue_timeout_secs)

    async def partial(self, text):
        if not isinstance(text, str) or len(text) > 4096:
            raise ValueError("Invalid partial transcript")
        return await self.partials.put(text)

    async def run(self, source, speech):
        outcomes = []
        original_scorer = speech.scorer
        control_task = None
        input_workers = []
        voice = self.voice
        class BargeScorer:
            def probability(self, pcm):
                nonlocal control_task
                probability = original_scorer.probability(pcm)
                if voice.barge_in_due(probability, len(pcm) / 32):
                    if control_task is None or control_task.done():
                        control_task = asyncio.create_task(voice.interrupt(owner=voice._response_task))
                return probability
            def reset(self):
                original_scorer.reset()
                voice._speech_ms = 0
        speech.scorer = BargeScorer()
        async def capture():
            try:
                async for pcm in source:
                    if not isinstance(pcm, bytes) or not 0 < len(pcm) <= 32000 or len(pcm) % 2:
                        raise VoiceFailure(VoiceErrorCode.CAPTURE)
                    await self.audio.put(pcm)
                    if self.audio.metrics.dropped_oldest:
                        raise VoiceFailure(VoiceErrorCode.ASR)
            except WebSocketDisconnect:
                await self.voice.disconnect()
                # Terminal transport loss must also stop pending ASR. Normal
                # source EOF still drains recognition/finals below.
                for worker in input_workers:
                    worker.cancel()
            finally:
                await self.audio.close()
        async def recognize():
            try:
                async for pcm in self.audio:
                    for final in await speech.feed(pcm):
                        if not await self.finals.put(final):
                            raise QueuePressure()
                for final in await speech.finish(pad_tail=True):
                    if not await self.finals.put(final):
                        raise QueuePressure()
            finally:
                await self.finals.close()
        async def respond():
            async for final in self.finals:
                # Own a task per turn, not the long-lived final-transcript consumer.
                turn = asyncio.create_task(self.voice.respond(final))
                try:
                    outcomes.append(await turn)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    if self.voice.disconnected:
                        return
                    if self.voice._stop_reason != "interrupted":
                        raise
                if len(outcomes) >= 60:
                    return
        try:
            remaining = 300 - (self.voice.clock() - self.voice._started)
            if remaining <= 0:
                raise VoiceFailure(VoiceErrorCode.CAPTURE)
            async with asyncio.timeout(remaining):
                async with asyncio.TaskGroup() as group:
                    group.create_task(capture())
                    input_workers.append(group.create_task(recognize()))
                    input_workers.append(group.create_task(respond()))
        except BaseException as error:
            # Input loss/ASR failure is terminal too: never leave old output buffered.
            try:
                await self.voice.disconnect()
            except Exception:  # noqa: BLE001 -- preserve the original safe input failure.
                pass
            close = getattr(self.voice.speaker, "close", None)
            if close:
                try:
                    async with asyncio.timeout(self.voice.streaming.cleanup_timeout_secs):
                        await close()
                except Exception:  # noqa: BLE001
                    pass
            if isinstance(error, TimeoutError):
                raise VoiceFailure(VoiceErrorCode.CAPTURE) from None
            raise
        finally:
            await self.close()
            speech.scorer = original_scorer
            speech.reset()
            if control_task is not None:
                await asyncio.gather(control_task, return_exceptions=True)
        return outcomes

    async def close(self):
        for channel in (self.partials, self.audio, self.finals):
            await channel.close(discard=True)
            self.voice.queue_metrics[channel.name] = channel.snapshot()
            with log_context(call_id=self.voice.state.call_sid, correlation_id=self.voice.correlation_id,
                             stage=self.voice.state.stage.value, language=self.voice.state.language_router.current.value):
                log_event(LogEvent.QUEUE_SUMMARY, queue_name=channel.name, queue_metrics=channel.snapshot())
