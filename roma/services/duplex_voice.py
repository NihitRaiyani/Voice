"""Continuous, bounded near-end capture independent of decode and spoken output.

The device supplies AEC-cleaned PCM and physical frame-end timestamps. Only this
capture loop owns VAD/endpoint state. ASR and response tasks may wait without
preventing the next microphone frame from being scored.
"""

import asyncio
import inspect
import math
import queue as thread_queue
import threading
import weakref
from dataclasses import replace

from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
from roma.domain.audio.endpointing import FRAME_BYTES, FRAME_MS, EndpointDetector
from roma.providers.audio.capture_buffer import CaptureFrame

_SCORER_OWNERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_SCORER_LOCK = threading.Lock()


class _SerialScorer:
    """One leased recurrent scorer; a poisoned owner cannot be reused.

    The daemon is independent of asyncio's default executor. A hung native call
    may remain alive, but cannot hang executor shutdown or accept another call.
    """
    def __init__(self, scorer):
        with _SCORER_LOCK:
            if scorer in _SCORER_OWNERS:
                raise VoiceFailure(VoiceErrorCode.CAPTURE)
            _SCORER_OWNERS[scorer] = self
        self.scorer = scorer
        self.items = thread_queue.Queue(maxsize=1)
        self.idle = threading.Event()
        self.idle.set()
        self.poisoned = False
        self.closed = False
        self.thread = threading.Thread(target=self._work, name="roma-vad", daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            with _SCORER_LOCK:
                del _SCORER_OWNERS[scorer]
            raise VoiceFailure(VoiceErrorCode.CAPTURE) from None

    def _work(self):
        while (item := self.items.get()) is not None:
            pcm, loop, future = item
            result = error = None
            try:
                result = self.scorer.probability(pcm)
            except Exception as exc:  # noqa: BLE001 -- publish failure to the owning waiter.
                error = exc
            finally:
                self.idle.set()
            def deliver(result=result, error=error, future=future):
                if not future.done():
                    if error is not None:
                        future.set_exception(error)
                    else:
                        future.set_result(result)
            try:
                loop.call_soon_threadsafe(deliver)
            except RuntimeError:
                pass
            if self.closed:
                return

    def submit(self, pcm):
        if self.closed or self.poisoned or not self.idle.is_set():
            raise VoiceFailure(VoiceErrorCode.CAPTURE)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.idle.clear()
        self.items.put_nowait((pcm, loop, future))
        return future

    def close(self, *, poison=False):
        self.poisoned |= poison
        if not self.closed:
            self.closed = True
            try:
                self.items.put_nowait(None)
            except thread_queue.Full:
                pass  # The queued operation exits after observing closed.
        if not self.poisoned:
            with _SCORER_LOCK:
                if _SCORER_OWNERS.get(self.scorer) is self:
                    del _SCORER_OWNERS[self.scorer]


class _SessionComplete(Exception):
    pass


class DuplexVoiceSession:
    def __init__(self, voice, speech, *, policy=None, max_turns=60,
                 on_result=None, on_capture_started=None, clock=None, remaining=None):
        if type(max_turns) is not int or not 1 <= max_turns <= 60:
            raise ValueError("Expected one to sixty turns")
        self.voice, self.speech = voice, speech
        self.policy = policy or speech.policy
        config = speech.endpoint.config
        if self.policy is not None:
            config = replace(config, silence_ms=self.policy.endpoint_wait_ms,
                             max_utterance_ms=self.policy.max_utterance_ms)
        elif config.max_utterance_ms > 30000:
            config = replace(config, max_utterance_ms=30000)
        self.endpoint = EndpointDetector(config)
        self.max_turns = max_turns
        self.on_result, self.on_capture_started = on_result, on_capture_started
        self.clock = clock or voice.clock
        self._deadline = voice._started + 300
        self.remaining = remaining or (lambda: self._deadline - self.clock())
        self.decoder = speech.segment_decoder(epoch_guard=voice.epochs)
        self._segments = asyncio.Queue(maxsize=2)
        self._finals = asyncio.Queue(maxsize=2)
        self._control = None
        self._decode_task = None
        self._response = None
        self._used = False
        self._outcomes = []
        self._attempts = 0
        self._score_task = None
        self._scorer_worker = None
        self._carry = bytearray()
        self._epoch = None
        self._origin = None
        self._handoff_until = None
        self._speech_ms = 0
        self._overlap = False
        self._source_samples = 0
        self._last_at = None
        self._generation = None

    def _call_ended(self):
        state = getattr(self.voice, "state", None)
        return bool(getattr(state, "lead_wants_out", False))

    def _check_deadline(self):
        remaining = self.remaining()
        if not isinstance(remaining, (float, int)) or not math.isfinite(remaining) or remaining <= 0:
            raise VoiceFailure(VoiceErrorCode.CAPTURE)
        return min(300, remaining)

    async def _score(self, pcm):
        # One owned inference at a time. Cancellation of the waiter never
        # releases a recurrent scorer while native work can still mutate it.
        if self._score_task is not None and not self._score_task.done():
            raise VoiceFailure(VoiceErrorCode.CAPTURE)
        if self._scorer_worker is None:
            self._scorer_worker = _SerialScorer(self.speech.scorer)
        self._score_task = self._scorer_worker.submit(pcm)
        try:
            async with asyncio.timeout(min(1., self._check_deadline())):
                return await asyncio.shield(self._score_task)
        except TimeoutError:
            raise VoiceFailure(VoiceErrorCode.CAPTURE) from None

    async def _join_scorer(self, wait_secs):
        task = self._score_task
        if task is not None and not task.done():
            _done, pending = await asyncio.wait({task}, timeout=wait_secs)
            if pending:
                task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
                self._scorer_worker.close(poison=True)
                return False
        if task is not None and not task.cancelled():
            task.exception()
        return True

    async def _notify(self, callback, *args):
        if callback is not None:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result

    async def recover_capture(self):
        """Discard a lost utterance while preserving the call and SQL authority."""
        self._check_deadline()
        if self.voice.disconnected or self._call_ended():
            raise _SessionComplete
        claim = getattr(self.voice, "claim_turn", self.voice.epochs.advance)
        epoch = claim()  # Invalidate late ASR/output before the first await.
        control, decode, response = self._control, self._decode_task, self._response
        self._control = None
        for task in (control, decode):
            if task is not None and not task.done():
                task.cancel()
        for queue in (self._segments, self._finals):
            while not queue.empty():
                queue.get_nowait()
        self._carry.clear()
        self.endpoint.reset()
        self._epoch = self._origin = self._handoff_until = None
        self._speech_ms = self._source_samples = 0
        self._overlap = False
        self._last_at = self._generation = None
        cleanup = self.voice.streaming.cleanup_timeout_secs if self.voice.streaming else 1
        async with asyncio.timeout(min(self._check_deadline(), cleanup * 3)):
            interrupted = await self.voice.interrupt(
                owner=response if response is not None and not response.done() else None,
                turn_epoch=epoch)
            if not interrupted:
                clear = getattr(self.voice.speaker, "clear", None)
                if clear is not None:
                    await clear()
            if response is not None and not response.done():
                response.cancel()
            tasks = {task for task in (control, decode, response) if task is not None}
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=cleanup)
                for task in pending:
                    task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
                if pending:
                    raise VoiceFailure(VoiceErrorCode.CAPTURE)
                for task in done:
                    if not task.cancelled():
                        task.result()
        self.speech.reset()
        self._decode_task = self._response = None
        self._check_deadline()

    def _claim(self, frame):
        old = self.voice.epochs.snapshot()
        claim = getattr(self.voice, "claim_turn", self.voice.epochs.advance)
        self._epoch = claim()
        # These are the only samples eligible for ownership handoff: already
        # physically captured, same generation, not submitted to any decoder.
        self._origin = old
        self._handoff_until = max(frame.at, self.clock())
        if self._decode_task is not None and not self._decode_task.done():
            self._decode_task.cancel()
        owner = self._response
        if owner is not None and not owner.done():
            if self._control is not None and not self._control.done():
                # Old control still owns playback. Capture continues; its join
                # precedes any new response, so a repeated onset cannot overtake it.
                return
            self._control = self._group.create_task(
                self.voice.interrupt(owner=owner, turn_epoch=self._epoch))

    def _enqueue(self, segments, frame):
        for segment in segments:
            if not segment.pcm:
                continue
            if self._epoch is None:
                if self._overlap:
                    # Speech that began during a response must satisfy the
                    # sustained-speech gate before it can interrupt. An ended
                    # short overlap stays below the interruption threshold;
                    # it cannot become a controller turn/booking affirmation.
                    self._overlap = False
                    self._speech_ms = 0
                    continue
                self._claim(frame)
            if segment.reason == "max_duration":
                # Full-duplex capture has a hard bounded turn, not an unbounded
                # accumulation of thirty-second ASR chunks.
                segment = replace(segment, reason="deadline")
                self.endpoint.discontinuity()
            keep = min(len(segment.pcm) // 2,
                       max(0, self._source_samples - segment.start_sample))
            segment = replace(segment, pcm=segment.pcm[:keep * 2],
                              end_sample=segment.start_sample + keep)
            if not segment.pcm:
                continue
            last = min(segment.last_speech_sample or segment.end_sample, self._source_samples)
            speech_end = frame.at - (self._source_samples - last) / 16000
            try:
                self._segments.put_nowait((segment, self._epoch, speech_end))
            except asyncio.QueueFull:
                raise VoiceFailure(VoiceErrorCode.ASR) from None
            self._attempts += 1
            self._epoch = self._origin = self._handoff_until = None
            self._speech_ms = 0
            self._overlap = False

    async def _capture(self, source):
        try:
            await self._notify(self.on_capture_started)
            async for frame in source:
                self._check_deadline()
                if self.voice.disconnected:
                    raise _SessionComplete
                if self._call_ended():
                    # The owning response still has to speak its sign-off.
                    # Drain capture without admitting another caller or barge.
                    if self._response is not None:
                        await asyncio.sleep(0)
                        continue
                    raise _SessionComplete
                if (not isinstance(frame, CaptureFrame) or not isinstance(frame.pcm, bytes)
                        or len(frame.pcm) != 640 or type(frame.epoch) is not int
                        or type(frame.generation) is not int or frame.generation < 0
                        or not isinstance(frame.at, (int, float)) or not math.isfinite(frame.at)):
                    raise VoiceFailure(VoiceErrorCode.CAPTURE)
                if self._generation is None:
                    self._generation = frame.generation
                if frame.generation < self._generation:
                    if self._epoch is not None:
                        raise VoiceFailure(VoiceErrorCode.CAPTURE)
                    continue
                if frame.generation != self._generation:
                    raise VoiceFailure(VoiceErrorCode.CAPTURE)
                current = self.voice.epochs.is_current(frame.epoch)
                handoff = (self._epoch is not None and frame.epoch == self._origin
                           and self._handoff_until is not None and frame.at <= self._handoff_until)
                if not current and not handoff:
                    # Never stamp stale callback PCM with a new turn identity.
                    if self._epoch is not None:
                        raise VoiceFailure(VoiceErrorCode.CAPTURE)
                    continue
                if self._last_at is not None and frame.at <= self._last_at:
                    raise VoiceFailure(VoiceErrorCode.CAPTURE)
                self._last_at = frame.at
                self._source_samples += len(frame.pcm) // 2
                self._carry.extend(frame.pcm)
                while len(self._carry) >= FRAME_BYTES:
                    pcm = bytes(self._carry[:FRAME_BYTES])
                    del self._carry[:FRAME_BYTES]
                    probability = await self._score(pcm)
                    before = self.endpoint.state
                    segments = self.endpoint.feed(pcm, probability)
                    busy = self._response is not None and not self._response.done()
                    threshold = self.voice.streaming.speech_threshold if self.voice.streaming else .8
                    minimum = self.voice.streaming.minimum_speech_ms if self.voice.streaming else 600
                    if before == "idle" and self.endpoint.state in {"starting", "speaking"}:
                        self._overlap = busy
                    self._speech_ms = self._speech_ms + FRAME_MS if probability >= threshold else 0
                    onset = before in {"idle", "starting"} and self.endpoint.state in {"speaking", "ending"}
                    if self._epoch is None and (
                            (onset and not busy and not self._overlap)
                            or ((busy or self._overlap) and self._speech_ms >= minimum)):
                        self._claim(frame)
                    self._enqueue(segments, frame)
                    if self._attempts >= self.max_turns:
                        # The lab limit bounds eligible attempts, including
                        # interrupted replies. Finish its final admitted reply;
                        # do not invalidate an in-flight SQL commit or playback.
                        close_source = getattr(source, "aclose", None)
                        if close_source is not None:
                            await close_source()
                        await self._segments.put(None)
                        return
                # A ready source must still yield to independent decode/control.
                await asyncio.sleep(0)
            self._check_deadline()
            if self._call_ended():
                await self._segments.put(None)
                return
            if self._carry:
                # Padding is exclusively a model-frame accommodation. _enqueue
                # trims back to the original source sample boundary before ASR.
                pcm = bytes(self._carry).ljust(FRAME_BYTES, b"\0")
                self._carry.clear()
                probability = await self._score(pcm)
                self._enqueue(self.endpoint.feed(pcm, probability), frame)
            if self._last_at is not None:
                self._enqueue(self.endpoint.flush(), frame)
            await self._segments.put(None)
        except BaseException:
            raise

    async def _decode(self):
        while (item := await self._segments.get()) is not None:
            segment, epoch, speech_end = item
            if self._call_ended() or not self.voice.epochs.is_current(epoch):
                continue
            self._check_deadline()
            self._decode_task = asyncio.create_task(self._transcribe(segment, epoch))
            try:
                final = await self._decode_task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                continue
            if final is not None and not self._call_ended() and self.voice.epochs.is_current(epoch):
                try:
                    self._finals.put_nowait(replace(final, speech_end_at=speech_end))
                except asyncio.QueueFull:
                    raise VoiceFailure(VoiceErrorCode.ASR) from None
        await self._finals.put(None)

    async def _transcribe(self, segment, epoch):
        if self._call_ended():
            return None
        self._check_deadline()
        return await self.decoder.transcribe_segment(segment, turn_epoch=epoch)

    async def _respond(self):
        while (final := await self._finals.get()) is not None:
            if self._call_ended():
                raise _SessionComplete
            if self._control is not None:
                try:
                    await self._control
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    if self.voice.disconnected or self._call_ended():
                        raise _SessionComplete from None
                # A child control may consume cancellation while finishing
                # teardown. Its return cannot authorize new work for this task.
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
            if not self.voice.epochs.is_current(final.turn_epoch):
                continue
            self._check_deadline()
            self._response = asyncio.create_task(self._deliver(final))
            try:
                outcome = await self._response
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                if self.voice.disconnected or self._call_ended():
                    raise _SessionComplete from None
                continue
            if self.voice.epochs.is_current(final.turn_epoch):
                self._outcomes.append(outcome)
                await self._notify(self.on_result, outcome)
                if self._call_ended() or len(self._outcomes) >= self.max_turns:
                    raise _SessionComplete

    async def _deliver(self, final):
        # Admission is checked inside the scheduled task as well: terminal
        # state may change while a previous output clear is being awaited.
        if self._call_ended():
            raise _SessionComplete
        self._check_deadline()
        return await self.voice.respond(final)

    async def run(self, source):
        if self._used:
            raise RuntimeError("A duplex session runs once")
        self._used = True
        terminal = False
        graceful = False
        try:
            if self._call_ended():
                graceful = True
                return self._outcomes
            self._check_deadline()
            try:
                async with asyncio.timeout(self._check_deadline()):
                    async with asyncio.TaskGroup() as group:
                        self._group = group
                        group.create_task(self._capture(source))
                        group.create_task(self._decode())
                        group.create_task(self._respond())
            except* _SessionComplete:
                graceful = True
        except BaseException:
            terminal = True
            raise
        finally:
            cleanup = self.voice.streaming.cleanup_timeout_secs if self.voice.streaming else 1
            try:
                try:
                    close_source = getattr(source, "aclose", None)
                    if close_source:
                        async with asyncio.timeout(cleanup):
                            await close_source()
                except BaseException:
                    terminal = True
                    raise
                finally:
                    if terminal:
                        try:
                            async with asyncio.timeout(cleanup * 3):
                                await self.voice.disconnect()
                        finally:
                            close = getattr(self.voice.speaker, "close", None)
                            if close:
                                async with asyncio.timeout(cleanup):
                                    await close()
                    elif graceful:
                        # The CLI owns successful finish/pause persistence. A
                        # configured turn limit ends capture without recording
                        # a failed disconnect before that checkpoint is written.
                        self.voice.epochs.advance()
                        clear = getattr(self.voice.speaker, "clear", None)
                        if clear:
                            async with asyncio.timeout(cleanup):
                                await clear()
            finally:
                self._carry.clear()
                self.endpoint.reset()
                if await self._join_scorer(cleanup):
                    try:
                        self.speech.reset()
                    finally:
                        if self._scorer_worker is not None:
                            self._scorer_worker.close()
        return self._outcomes


__all__ = ["CaptureFrame", "DuplexVoiceSession"]
