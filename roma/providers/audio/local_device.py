"""Persistent local capture with optional full-duplex echo cancellation."""

import asyncio
import math
import sys
import threading
from dataclasses import replace
from time import perf_counter

from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
from roma.domain.audio.pcm import validate_pcm16
from roma.domain.audio.turn_policy import TurnPolicy
from roma.providers.ai.contracts import TTSResult
from roma.providers.audio.capture_buffer import CaptureBuffer, CaptureDiscontinuity


async def _finish_native(task):
    """Join native teardown despite repeated caller cancellation, then propagate it."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done() and task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


class LocalAudioDevice:
    def __init__(
        self,
        *,
        input_device=None,
        output_device=None,
        backend=None,
        policy=None,
        check_active=None,
        epoch_guard=None,
        capture_capacity=32,
        capture_watchdog_secs=1.0,
        clock=perf_counter,
        duplex=False,
        echo=None,
    ):
        if not 0 < capture_watchdog_secs <= 10:
            raise ValueError("Capture watchdog must be bounded")
        if type(duplex) is not bool or (duplex and echo is None):
            raise ValueError("Full duplex requires an explicit echo canceller")
        self.duplex, self.echo = duplex, echo
        self._input_samples = 0
        self._input_anchor: float | None = None
        self._adc_offset: float | None = None
        self._last_capture_end: float | None = None
        self._render_next_at: float | None = None
        self.input_device, self.output_device = input_device, output_device
        self._backend = backend
        self.policy = policy or TurnPolicy()
        self.first_write_at = None
        self.check_active = check_active
        self.epochs = epoch_guard
        self.clock = clock
        self.capture_watchdog_secs = capture_watchdog_secs
        self.capture = CaptureBuffer(capture_capacity, clock=clock)
        self._input_stream = None
        self._start_task = None
        self._close_task = None
        self._start_lock = asyncio.Lock()
        self._started = False
        self._closing = False
        self._listening = False
        self._capture_deadline = None
        self._play_lock = asyncio.Lock()
        self._output = None
        self._output_generation = 0
        self._echo_generation = 0
        self._echo_epoch = self._epoch()
        self._echo_lock = threading.Lock()
        self.last_clear_ms = None
        self._callback_at = None
        self._callback_count = 0
        self._callback_interval_max_us = 0.0
        self._input_overflows = 0
        self._output_underflows = 0
        self._sequence_gaps = 0
        self._heartbeat_task = None
        self._loop_stall_max_ms = 0.0
        self._loop_heartbeat_samples = 0
        self._capture_frame_age_max_ms = 0.0
        self._capture_recoveries = 0

    def bind_epoch_guard(self, guard):
        if self._listening:
            raise RuntimeError("Cannot replace epoch ownership while capturing")
        self.epochs = guard

    def _epoch(self, epoch=None):
        return epoch if epoch is not None else (self.epochs.snapshot() if self.epochs else 0)

    def _require_epoch(self, epoch):
        if self.epochs is not None:
            self.epochs.require_current(epoch)

    def _check_active(self):
        if self.check_active is not None:
            self.check_active()

    @property
    def capture_mode(self):
        return self.capture.mode

    @property
    def capture_health(self):
        """Best-effort lifetime metadata; maxima are not acoustic acceptance."""
        return {
            "scope": "device_lifetime",
            "audio_callback_count": self._callback_count,
            "audio_cb_interval_max_us": self._callback_interval_max_us,
            "portaudio_input_overflows": self._input_overflows,
            "portaudio_output_underflows": self._output_underflows,
            "pcm_sequence_gaps": self._sequence_gaps,
            "asyncio_loop_stall_max_ms": self._loop_stall_max_ms,
            "asyncio_loop_heartbeat_samples": self._loop_heartbeat_samples,
            "capture_frame_age_max_ms": self._capture_frame_age_max_ms,
            "capture_recoveries": self._capture_recoveries,
            **self.capture.health(),
        }

    def _audio(self):
        if sys.byteorder != "little":
            raise ValueError("Local PCM device requires a little-endian host")
        if self._backend is None:
            import sounddevice

            self._backend = sounddevice
        return self._backend

    def _capture_callback(self, pcm, frames, time_info, status):
        # Hardware ADC/sample time keeps DSP aligned when Python callbacks jitter.
        at = self.clock()
        self._callback_count += 1
        if self._callback_at is not None:
            self._callback_interval_max_us = max(
                self._callback_interval_max_us, max(0.0, at - self._callback_at) * 1_000_000)
        self._callback_at = at
        overflow = bool(getattr(status, "input_overflow", False))
        self._input_overflows += int(overflow)
        if self.duplex:
            adc = getattr(time_info, "inputBufferAdcTime", None)
            current = getattr(time_info, "currentTime", None)
            if (type(adc) in (int, float) and type(current) in (int, float)
                    and math.isfinite(adc) and math.isfinite(current) and 0 < adc <= current):
                if self._adc_offset is None:
                    self._adc_offset = at - current
                at = self._adc_offset + adc + frames / 16000
            else:
                if self._input_anchor is None:
                    self._input_anchor = at - frames / 16000
                self._input_samples += frames
                at = self._input_anchor + self._input_samples / 16000
            if self._last_capture_end is not None and abs(at - self._last_capture_end - frames / 16000) > 0.005:
                overflow = True
                self._sequence_gaps += 1
            self._last_capture_end = at
        if self._capture_deadline is not None and at > self._capture_deadline:
            return
        self.capture.push(pcm, frames, self._epoch(), overflow, at=at)

    async def _monitor_loop(self):
        # An ordinary event-loop timer: diagnostic, not a real-time scheduler.
        loop = asyncio.get_running_loop()
        previous = loop.time()
        while True:
            await asyncio.sleep(0.010)
            now = loop.time()
            self._loop_stall_max_ms = max(self._loop_stall_max_ms, max(0.0, now - previous - 0.010) * 1000)
            self._loop_heartbeat_samples += 1
            previous = now

    async def start(self):
        async with self._start_lock:
            if self._closing:
                raise RuntimeError("Local audio device is closed")
            if self._started:
                return
            self._check_active()
            if self.duplex:
                self.echo.preflight()
            self._input_stream = self._audio().RawInputStream(
                samplerate=16000,
                channels=1,
                dtype="int16",
                blocksize=320,
                device=self.input_device,
                callback=self._capture_callback,
            )
            self._start_task = asyncio.create_task(asyncio.to_thread(self._input_stream.start))
            try:
                await asyncio.shield(self._start_task)
                self._check_active()
                self._started = True
                self._heartbeat_task = asyncio.create_task(self._monitor_loop(), name="capture-heartbeat")
            except BaseException:
                await self.aclose()
                raise

    async def _close(self):
        self.capture.close()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        try:
            await self.clear()
        finally:
            await self._close_input()

    async def _close_input(self):
        stream = self._input_stream
        if stream is not None:
            try:
                await asyncio.to_thread(stream.abort)
            finally:
                try:
                    if self._start_task is not None:
                        await asyncio.gather(self._start_task, return_exceptions=True)
                finally:
                    await asyncio.to_thread(stream.close)

    async def aclose(self):
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await _finish_native(self._close_task)

    async def frames(self, *, max_seconds=300, on_capture_started=None, on_discontinuity=None):
        """Continuous AEC-cleaned capture; no ASR/model work runs in the callback."""
        if not self.duplex or not self._started or self._closing:
            raise RuntimeError("A started full-duplex device is required")
        if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or not 0 < max_seconds <= 300:
            raise ValueError("Invalid duplex capture deadline")
        if self._listening:
            raise RuntimeError("Only one capture consumer is allowed")
        self._check_active()
        self._listening = True
        started = self.clock()
        self._capture_deadline = started + max_seconds
        generation = self.capture.activate(self._epoch(), follow_epochs=True)
        recoveries = 0
        try:
            if on_capture_started is not None:
                on_capture_started()
            while not self._closing:
                self._check_active()
                try:
                    frame = self.capture.pop(generation)
                    if frame is None:
                        if self.clock() >= self._capture_deadline:
                            break
                        if self.clock() - max(started, self.capture.last_callback_at) >= self.capture_watchdog_secs:
                            raise VoiceFailure(VoiceErrorCode.DEVICE_CAPTURE_STALLED)
                        await asyncio.sleep(0.005)
                        continue
                    self._capture_frame_age_max_ms = max(
                        self._capture_frame_age_max_ms, max(0.0, self.clock() - frame.at) * 1000)
                    if frame.at > self._capture_deadline:
                        break
                    cleaned = await asyncio.to_thread(self._process_echo, frame.pcm, frame.at - 0.020)
                    if not isinstance(cleaned, bytes) or len(cleaned) != 640:
                        raise ValueError("Echo processing returned an invalid capture frame")
                    self.capture.check(generation)
                except CaptureDiscontinuity:
                    self.capture.mute()
                    if on_discontinuity is None or recoveries >= 3:
                        raise
                    remaining = self._capture_deadline - self.clock()
                    if remaining <= 0:
                        raise VoiceFailure(VoiceErrorCode.CAPTURE) from None
                    recoveries += 1
                    self._capture_recoveries += 1
                    async with asyncio.timeout(min(3, remaining)):
                        await on_discontinuity()
                    self._check_active()
                    if self.clock() >= self._capture_deadline:
                        raise VoiceFailure(VoiceErrorCode.CAPTURE) from None
                    generation = self.capture.activate(self._epoch(), follow_epochs=True)
                    continue
                yield replace(frame, pcm=cleaned)
        finally:
            self.capture.mute()
            self._capture_deadline = None
            self._listening = False

    async def listen(self, speech, *, max_seconds=30, on_capture_started=None, epoch=None):
        if self.duplex:
            raise RuntimeError("Use continuous frames() for full duplex")
        if not 0 < max_seconds <= self.policy.max_utterance_ms / 1000:
            raise ValueError("Capture limit must be in (0,30] seconds")
        if not self._started or self._closing:
            raise RuntimeError("Explicit device.start() is required before listening")
        if self._listening:
            raise RuntimeError("Only one capture consumer is allowed")
        self._check_active()
        epoch = self._epoch(epoch)
        self._require_epoch(epoch)
        self._listening = True
        generation = self.capture.activate(epoch)
        started = self.clock()
        deadline = started + max_seconds
        self._capture_deadline = deadline
        processing_ms = 0.0
        samples_read = 0
        sample_limit = int(max_seconds * 16000)
        read_marks = []
        old_finalize = getattr(speech, "on_utterance_finalizing", None)

        def finalizing():
            self.capture.mute()
            if old_finalize is not None:
                old_finalize()

        speech.on_utterance_finalizing = finalizing
        if hasattr(speech, "epochs") and self.epochs is not None:
            speech.epochs = self.epochs

        def stamp(result):
            last = getattr(result, "last_speech_sample", None)
            if last is None:
                return result, {}
            for end, read_at in read_marks:
                if end >= last:
                    speech_end = read_at - (end - last) / 16000
                    return replace(result, speech_end_at=speech_end), {
                        "endpoint_wait_ms": max(
                            0.0, (self.clock() - speech_end) * 1000 - result.asr_ms
                        )
                    }
            raise ValueError("Invalid last speech frame timing")

        def metrics(result):
            result, endpoint = stamp(result)
            return result, {
                **endpoint,
                "capture_ms": max(0.0, (self.clock() - started) * 1000 - result.asr_ms),
                "vad_endpoint_ms": max(0.0, processing_ms - result.asr_ms),
            }

        try:
            if on_capture_started is not None:
                on_capture_started()
            # A delayed consumer must retain frames received by the deadline.
            # Wall time stops waiting for new callbacks, not draining this ring.
            while samples_read < sample_limit:
                self._check_active()
                self._require_epoch(epoch)
                if self._closing:
                    raise asyncio.CancelledError
                frame = self.capture.pop(generation, epoch)
                if frame is None:
                    if self.clock() >= deadline:
                        break
                    if (
                        self.clock() - max(started, self.capture.last_callback_at)
                        >= self.capture_watchdog_secs
                    ):
                        raise VoiceFailure(VoiceErrorCode.DEVICE_CAPTURE_STALLED)
                    await asyncio.sleep(0.005)
                    continue
                self._capture_frame_age_max_ms = max(
                    self._capture_frame_age_max_ms, max(0.0, self.clock() - frame.at) * 1000)
                if frame.at > deadline:
                    break
                block = frame.pcm[: min(320, sample_limit - samples_read) * 2]
                samples_read += len(block) // 2
                read_marks.append((samples_read, frame.at))
                tick = self.clock()
                results = (
                    await speech.feed(block, turn_epoch=epoch)
                    if hasattr(speech, "epochs")
                    else await speech.feed(block)
                )
                self._check_active()
                self._require_epoch(epoch)
                self.capture.check(generation)
                processing_ms += (self.clock() - tick) * 1000
                if results:
                    if len(results) != 1:
                        raise ValueError("Only one finalized microphone turn is admitted")
                    return metrics(results[0])
            self._check_active()
            self._require_epoch(epoch)
            self.capture.mute()
            tick = self.clock()
            results = (
                await speech.finish(pad_tail=True, endpoint_reason="deadline", turn_epoch=epoch)
                if hasattr(speech, "epochs")
                else await speech.finish(pad_tail=True, endpoint_reason="deadline")
            )
            self._check_active()
            self._require_epoch(epoch)
            self.capture.check(generation)
            processing_ms += (self.clock() - tick) * 1000
            if results:
                if len(results) != 1:
                    raise ValueError("Only one finalized microphone turn is admitted")
                if not results[0].text.strip() and results[0].error is None:
                    raise VoiceFailure(VoiceErrorCode.CAPTURE_DEADLINE_NO_TRANSCRIPT)
                return metrics(results[0])
            return None, {
                "capture_ms": (self.clock() - started) * 1000,
                "vad_endpoint_ms": processing_ms,
            }
        finally:
            self.capture.mute()
            self._capture_deadline = None
            speech.on_utterance_finalizing = old_finalize
            speech.reset()
            self._listening = False

    def _check_output(self, generation, epoch):
        self._check_active()
        self._require_epoch(epoch)
        if self._closing or generation != self._output_generation:
            raise asyncio.CancelledError

    async def _abort_output(self, output):
        if output["abort"] is None:
            output["abort"] = asyncio.create_task(asyncio.to_thread(output["stream"].abort))
        await asyncio.shield(output["abort"])

    async def _cleanup_output(self, output):
        try:
            await self._abort_output(output)
        finally:
            try:
                if output["io"] is not None:
                    await asyncio.gather(output["io"], return_exceptions=True)
            finally:
                try:
                    await asyncio.to_thread(output["stream"].close)
                finally:
                    output["done"].set()

    def _clear_echo(self):
        with self._echo_lock:
            self.echo.clear_future(at=self.clock())
            self._echo_generation = self._output_generation
            self._echo_epoch = self._epoch()

    def _process_echo(self, pcm, at):
        with self._echo_lock:
            # Invalidation is immediate; a delayed clear task cannot expose an
            # already-cancelled render reference to the capture DSP.
            if (self._echo_generation != self._output_generation
                    or (self.epochs is not None and not self.epochs.is_current(self._echo_epoch))):
                self.echo.clear_future(at=self.clock())
                self._echo_generation = self._output_generation
                self._echo_epoch = self._epoch()
            return self.echo.process(pcm, at=at)

    def _render_echo(self, block, stream, rate, generation, epoch):
        with self._echo_lock:
            self._check_output(generation, epoch)
            latency = getattr(stream, "latency", 0.0)
            if type(latency) not in (int, float) or not math.isfinite(latency) or not 0 <= latency <= 1:
                raise ValueError("Invalid native output latency")
            at = max(self.clock() + latency, self._render_next_at or 0)
            try:
                self.echo.render(block, rate=rate, at=at)
                self._render_next_at = at + len(block) / 2 / rate
            finally:
                if (generation != self._output_generation
                        or (self.epochs is not None and not self.epochs.is_current(epoch))):
                    self.echo.clear_future(at=self.clock())
                self._echo_generation = self._output_generation
                self._echo_epoch = self._epoch()
            self._check_output(generation, epoch)

    async def clear(self):
        # Invalidate writes synchronously, before joining any native or DSP work.
        self._output_generation += 1
        if not self.duplex:
            self.capture.mute()
        output, started = self._output, self.clock()

        async def finish():
            aborting = asyncio.create_task(self._abort_output(output)) if output is not None else None
            try:
                if self.duplex:
                    await asyncio.to_thread(self._clear_echo)
            finally:
                if aborting is not None:
                    try:
                        await aborting
                    finally:
                        await output["done"].wait()
                self.last_clear_ms = max(0.0, (self.clock() - started) * 1000)

        await _finish_native(asyncio.create_task(finish()))

    async def play(self, result: TTSResult, *, epoch=None):
        result_epoch = getattr(result, "turn_epoch", None)
        epoch = self._epoch(result_epoch if epoch is None else epoch)
        if result_epoch is not None and epoch != result_epoch:
            raise asyncio.CancelledError
        generation = self._output_generation
        self._check_output(generation, epoch)
        validate_pcm16(result.audio)
        if (
            not result.audio
            or result.format.channels != 1
            or result.format.encoding != "pcm_s16le"
        ):
            raise ValueError("Speaker requires nonempty mono PCM16")
        async with self._play_lock:
            self._check_output(generation, epoch)
            if not self.duplex:
                self.capture.mute()
            stream = self._audio().RawOutputStream(
                samplerate=result.format.sample_rate_hz,
                channels=1,
                dtype="int16",
                device=self.output_device,
            )
            output = {"stream": stream, "io": None, "abort": None, "done": asyncio.Event()}
            self._output = output
            self.first_write_at = None
            self._render_next_at = None

            def write(block):
                self._check_output(generation, epoch)
                if self.first_write_at is None:
                    self.first_write_at = self.clock()
                if self.duplex:
                    self._render_echo(block, stream, result.format.sample_rate_hz, generation, epoch)
                return stream.write(block)

            try:
                output["io"] = asyncio.create_task(asyncio.to_thread(stream.start))
                await asyncio.shield(output["io"])
                output["io"] = None
                self._check_output(generation, epoch)
                batch = result.format.sample_rate_hz // 50 * 2 if self.duplex else 3200
                for offset in range(0, len(result.audio), batch):
                    output["io"] = asyncio.create_task(
                        asyncio.to_thread(write, result.audio[offset : offset + batch])
                    )
                    underflow = await asyncio.shield(output["io"])
                    output["io"] = None
                    self._check_output(generation, epoch)
                    if underflow:
                        self._output_underflows += 1
                        raise ValueError("Speaker underflow")
                output["io"] = asyncio.create_task(asyncio.to_thread(stream.stop))
                await asyncio.shield(output["io"])
                output["io"] = None
                self._check_output(generation, epoch)
            finally:
                try:
                    await _finish_native(asyncio.create_task(self._cleanup_output(output)))
                finally:
                    try:
                        if self.duplex:
                            await asyncio.to_thread(self._clear_echo)
                    finally:
                        if self._output is output:
                            self._output = None
