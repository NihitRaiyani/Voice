"""Persistent local capture with optional full-duplex echo cancellation."""

import asyncio
import math
import sys
import threading
from dataclasses import replace
from time import perf_counter

from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
from roma.domain.audio.pcm import validate_pcm16
from roma.domain.audio.resampling import PCMResampler
from roma.domain.audio.turn_policy import TurnPolicy
from roma.providers.ai.contracts import TTSResult
from roma.providers.audio.callback_output import CallbackOutput
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
        self._duplex_latency = None
        self._shared_clock_uncertainty_frames = 0
        self._input_samples = 0
        self._input_anchor: float | None = None
        self._adc_offset: float | None = None
        self._last_capture_end: float | None = None
        self._adc_was_valid: bool | None = None
        self._adc_valid_count = 0
        self._adc_invalid_count = 0
        self._adc_source_changes = 0
        self._adc_anomalies = 0
        self._adc_phase_error_max_ms = 0.0
        self._adc_subframe_uncertainty_frames = 0
        self._adc_subframe_uncertainty_max_ms = 0.0
        self._adc_fullframe_uncertainty_frames = 0
        self._adc_fullframe_uncertainty_max_ms = 0.0
        self._adc_examples = []
        self._adc_calibration_max_ms = 0.0
        self._adc_clock_rebases = 0
        self._adc_clock_rebase_max_ms = 0.0
        self._adc_current_clock_error_max_ms = 0.0
        self._previous_adc = None
        self._previous_adc_current = None
        self._previous_adc_samples = 0
        self._previous_adc_callback = 0
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
        self._input_open_task = None
        self._start_task = None
        self._close_task = None
        self._start_lock = asyncio.Lock()
        self._started = False
        self._closing = False
        self._listening = False
        self._capture_deadline = None
        self._play_lock = asyncio.Lock()
        self._output = None
        self._output_buffer = None
        self._output_start_task = None
        self._output_ready = False
        self._playback_owner = None
        self._native_lock = threading.Lock()
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
    def playback_active(self):
        """Software playback with an actual callback handoff, never acoustic proof."""
        owner = self._playback_owner
        return bool(owner is not None and self._output_buffer is not None
                    and self._output_buffer.first_write_at is not None
                    and owner[0] == self._output_generation
                    and (self.epochs is None or self.epochs.is_current(owner[1])))

    @property
    def output_pending(self):
        """Queued software PCM or estimated handed DAC tail, independent of epoch."""
        output = self._output_buffer
        return bool(output is not None and (output.frames or output.pending_handoff
                    or (output.last_end_at is not None and self.clock() < output.last_end_at)))

    @property
    def first_write_at(self):
        output = getattr(self, "_output_buffer", None)
        return output.first_write_at if output is not None else getattr(self, "_first_write_at", None)

    @first_write_at.setter
    def first_write_at(self, value):
        self._first_write_at = value

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
            "portaudio_output_underflows": (self._output_buffer.underflows
                if self._output_buffer is not None else self._output_underflows),
            "pcm_sequence_gaps": self._sequence_gaps,
            "capture_adc_valid_timestamps": self._adc_valid_count,
            "capture_adc_invalid_timestamps": self._adc_invalid_count,
            "capture_adc_source_changes": self._adc_source_changes,
            "capture_adc_timestamp_anomalies": self._adc_anomalies,
            "capture_adc_phase_error_max_ms": self._adc_phase_error_max_ms,
            "capture_adc_subframe_uncertainty_frames": self._adc_subframe_uncertainty_frames,
            "capture_adc_subframe_uncertainty_max_ms": self._adc_subframe_uncertainty_max_ms,
            "capture_adc_fullframe_uncertainty_frames": self._adc_fullframe_uncertainty_frames,
            "capture_adc_fullframe_uncertainty_max_ms": self._adc_fullframe_uncertainty_max_ms,
            "capture_adc_calibration_max_ms": self._adc_calibration_max_ms,
            "capture_adc_clock_rebases": self._adc_clock_rebases,
            "capture_adc_clock_rebase_max_ms": self._adc_clock_rebase_max_ms,
            "capture_adc_current_clock_error_max_ms": self._adc_current_clock_error_max_ms,
            "capture_adc_anomaly_examples": [dict(row) for row in self._adc_examples],
            "asyncio_loop_stall_max_ms": self._loop_stall_max_ms,
            "asyncio_loop_heartbeat_samples": self._loop_heartbeat_samples,
            "capture_frame_age_max_ms": self._capture_frame_age_max_ms,
            "capture_recoveries": self._capture_recoveries,
            "duplex_shared_clock_uncertainty_frames": self._shared_clock_uncertainty_frames,
            "aec_reference_unknown_frames": getattr(getattr(self.echo, "reference", None), "unknown_frames", 0),
            "aec_reference_unknown_samples": getattr(getattr(self.echo, "reference", None), "unknown_samples", 0),
            "aec_reference_known_silent_samples": getattr(getattr(self.echo, "reference", None), "known_silent_samples", 0),
            **self.capture.health(),
            **(self._output_buffer.health() if self._output_buffer is not None else {}),
        }

    def _audio(self):
        if sys.byteorder != "little":
            raise ValueError("Local PCM device requires a little-endian host")
        if self._backend is None:
            import sounddevice

            self._backend = sounddevice
        return self._backend

    def _duplex_callback(self, pcm, outdata, frames, time_info, status):
        # One PortAudio stream owns both device clocks. Python callback-entry
        # delay shifts both mappings equally; it cannot become a differential
        # ADC/DAC alignment error as it could with separately opened streams.
        now = self.clock()
        self._capture_callback(pcm, frames, time_info, status, callback_at=now)
        adc = getattr(time_info, "inputBufferAdcTime", None)
        current = getattr(time_info, "currentTime", None)
        dac = getattr(time_info, "outputBufferDacTime", None)
        valid = all(type(value) in (int, float) and math.isfinite(value)
                    for value in (adc, current, dac))
        if valid and 0 < adc <= current <= dac and dac - adc <= 1:
            self._duplex_latency = dac - adc
        else:
            self._shared_clock_uncertainty_frames += 1
        output = self._output_buffer
        if output is None:
            outdata[:] = bytes(len(outdata))
            return
        if self._last_capture_end is None or self._duplex_latency is None:
            outdata[:] = bytes(len(outdata))
            output.error = "Shared ADC/DAC alignment unavailable"
            return
        if (type(current) not in (int, float) or type(dac) not in (int, float)
                or not math.isfinite(current) or not math.isfinite(dac)
                or not 0 <= dac - current <= 1):
            outdata[:] = bytes(len(outdata))
            output.error = "Invalid shared native output clock"
            return
        output.callback(outdata, frames, time_info, status, callback_at=now,
                        reference_at=self._last_capture_end - .020 + self._duplex_latency)

    def _capture_callback(self, pcm, frames, time_info, status, *, callback_at=None):
        # Hardware ADC/sample time keeps DSP aligned when Python callbacks jitter.
        at = self.clock() if callback_at is None else callback_at
        self._callback_count += 1
        callback_interval_ms = None if self._callback_at is None else (at - self._callback_at) * 1000
        if self._callback_at is not None:
            self._callback_interval_max_us = max(
                self._callback_interval_max_us, max(0.0, at - self._callback_at) * 1_000_000)
        self._callback_at = at
        overflow = bool(getattr(status, "input_overflow", False))
        self._input_overflows += int(overflow)
        if self.duplex and type(frames) is int and frames == 320:
            duration = frames / 16000
            adc = getattr(time_info, "inputBufferAdcTime", None)
            current = getattr(time_info, "currentTime", None)
            adc_valid = (type(adc) in (int, float) and type(current) in (int, float)
                         and math.isfinite(adc) and math.isfinite(current) and 0 < adc <= current)
            self._adc_valid_count += int(adc_valid)
            self._adc_invalid_count += int(not adc_valid)
            source_change = self._adc_was_valid is not None and adc_valid != self._adc_was_valid
            self._adc_source_changes += int(source_change)
            self._adc_was_valid = adc_valid
            # One sample clock covers every block, including timestamp outages.
            # The independent fallback anchor previously jumped to callback wall
            # time and counted only ADC-invalid blocks, manufacturing PCM gaps.
            if self._input_anchor is None:
                self._input_anchor = at - duration
                if adc_valid:
                    self._adc_offset = at - current
                    self._input_anchor = self._adc_offset + adc
            self._input_samples += frames
            at = self._input_anchor + self._input_samples / 16000
            phase_error = adc_delta_ms = current_delta_ms = expected_adc_delta_ms = None
            current_clock_error_ms = adc_current_delta_error_ms = None
            clock_rebased = consecutive_adc = False
            if adc_valid:
                if self._adc_offset is None:
                    # A first usable ADC stamp calibrates to the existing clock;
                    # delayed callback wall time cannot establish a second anchor.
                    self._adc_offset = at - adc - duration
                phase_error = self._adc_offset + adc + duration - at
                self._adc_phase_error_max_ms = max(self._adc_phase_error_max_ms, abs(phase_error) * 1000)
                if self._previous_adc is not None:
                    adc_delta_ms = (adc - self._previous_adc) * 1000
                    current_delta_ms = (current - self._previous_adc_current) * 1000
                    expected_adc_delta_ms = (self._input_samples - self._previous_adc_samples) / 16
                    # Compare matching intervals only; a timestamp outage spans
                    # more blocks than the immediately preceding wall interval.
                    consecutive_adc = self._previous_adc_callback == self._callback_count - 1
                    if consecutive_adc:
                        adc_current_delta_error_ms = adc_delta_ms - current_delta_ms
                        current_clock_error_ms = current_delta_ms - callback_interval_ms
                        self._adc_current_clock_error_max_ms = max(
                            self._adc_current_clock_error_max_ms, abs(current_clock_error_ms))
                self._previous_adc, self._previous_adc_current = adc, current
                self._previous_adc_samples = self._input_samples
                self._previous_adc_callback = self._callback_count
                anomalous = abs(phase_error) > 0.005
                self._adc_anomalies += int(anomalous)
                # Timestamp estimates are telemetry, not proof of missing PCM.
                # Delivered frames own continuity; only native or application
                # loss signals below may invalidate an admitted utterance.
                # PortAudio ADC/currentTime share a clock domain. A paired step
                # against monotonic callback time is not evidence of missing PCM.
                # Rebase only with independent, bounded common-clock evidence:
                # consecutive full blocks, paced (10–40ms) callbacks, agreeing
                # hardware-clock deltas and a same-direction >5ms clock mismatch.
                # Uncorroborated disagreement remains visible without retiming
                # the delivered sample clock or manufacturing an overflow flag.
                clock_rebased = (
                    anomalous and not overflow and len(pcm) == frames * 2
                    and consecutive_adc and expected_adc_delta_ms == 20
                    and 10 <= callback_interval_ms < 40
                    and abs(phase_error) <= 0.100
                    and abs(adc_current_delta_error_ms) <= 5
                    and abs(current_clock_error_ms) > 5
                    and phase_error * current_clock_error_ms > 0
                )
                if clock_rebased:
                    self._adc_offset -= phase_error
                    self._adc_clock_rebases += 1
                    self._adc_clock_rebase_max_ms = max(self._adc_clock_rebase_max_ms, abs(phase_error) * 1000)
                elif abs(phase_error) >= duration - 1e-9:
                    self._adc_fullframe_uncertainty_frames += 1
                    self._adc_fullframe_uncertainty_max_ms = max(
                        self._adc_fullframe_uncertainty_max_ms, abs(phase_error) * 1000)
                elif anomalous:
                    self._adc_subframe_uncertainty_frames += 1
                    self._adc_subframe_uncertainty_max_ms = max(
                        self._adc_subframe_uncertainty_max_ms, abs(phase_error) * 1000)
                else:
                    # Track slow clock drift with at most 50us correction/block;
                    # an anomalous estimate never directly moves accepted PCM.
                    correction = max(-0.00005, min(0.00005, phase_error / 64))
                    self._input_anchor += correction
                    at += correction
                    self._adc_calibration_max_ms = max(self._adc_calibration_max_ms, abs(correction) * 1000)
            if (source_change or (phase_error is not None and abs(phase_error) > 0.005)) and len(self._adc_examples) < 8:
                self._adc_examples.append({
                    "callback": self._callback_count,
                    "adc_valid": adc_valid,
                    "adc_delta_ms": adc_delta_ms,
                    "current_delta_ms": current_delta_ms,
                    "expected_adc_delta_ms": expected_adc_delta_ms,
                    "phase_error_ms": None if phase_error is None else phase_error * 1000,
                    "callback_interval_ms": callback_interval_ms,
                    "current_clock_delta_error_ms": current_clock_error_ms,
                    "adc_current_delta_error_ms": adc_current_delta_error_ms,
                    "clock_rebased": clock_rebased,
                    "clock_rebase_ms": phase_error * 1000 if clock_rebased else None,
                    "native_input_overflow": bool(getattr(status, "input_overflow", False)),
                    "source_change": source_change,
                    "rejected": overflow,
                })
            self._last_capture_end = at
        if self._capture_deadline is not None and at > self._capture_deadline and not overflow:
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

    async def _native(self, function):
        # Lifecycle APIs share one serial owner; callbacks never take this lock.
        def invoke():
            with self._native_lock:
                return function()
        return await asyncio.to_thread(invoke)

    async def start(self):
        async with self._start_lock:
            if self._closing:
                raise RuntimeError("Local audio device is closed")
            if self._started:
                return
            self._check_active()
            if self.duplex:
                self.echo.preflight()
            if self.duplex:
                self._output_buffer = CallbackOutput(
                    generation=lambda: self._output_generation,
                    current_epoch=lambda epoch: self.epochs is None or self.epochs.is_current(epoch),
                    clock=self.clock,
                )
                require_coverage = getattr(self.echo, "require_reference_coverage", None)
                if require_coverage is not None:
                    require_coverage()
            def factory():
                if self.duplex:
                    return self._audio().RawStream(
                        samplerate=16000, channels=1, dtype="int16", blocksize=320,
                        device=(self.input_device, self.output_device), callback=self._duplex_callback)
                return self._audio().RawInputStream(
                    samplerate=16000, channels=1, dtype="int16", blocksize=320,
                    device=self.input_device, callback=self._capture_callback)
            self._input_open_task = asyncio.create_task(self._native(factory))
            try:
                self._input_stream = await asyncio.shield(self._input_open_task)
                self._start_task = asyncio.create_task(asyncio.to_thread(self._input_stream.start))
                await asyncio.shield(self._start_task)
                self._check_active()
                if self.duplex:
                    self._output = self._input_stream
                    self._output_ready = True
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
            try:
                await self._close_output()
            finally:
                await self._close_input()

    async def _close_input(self):
        if self._input_stream is None and self._input_open_task is not None:
            # Shielded construction can outlive its cancelled start caller. Join
            # and adopt the resulting stream before exactly-once native cleanup.
            result = await asyncio.gather(self._input_open_task, return_exceptions=True)
            if not isinstance(result[0], BaseException):
                self._input_stream = result[0]
        stream = self._input_stream
        if stream is not None:
            try:
                await asyncio.to_thread(stream.abort)
            finally:
                try:
                    if self._start_task is not None:
                        await asyncio.gather(self._start_task, return_exceptions=True)
                finally:
                    await self._native(stream.close)

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

    def _drain_render_receipts(self):
        """Called under the DSP lock, outside either native callback."""
        output = self._output_buffer
        if output is None:
            return
        known_until = output.reference_known_until
        while (receipt := output.pop_receipt()) is not None:
            pcm, at = receipt
            if self.duplex:
                self.echo.render(pcm, rate=16000, at=at)
        reference = getattr(self.echo, "reference", None)
        if reference is not None and known_until is not None:
            reference.advance_through(known_until)

    def _clear_echo(self):
        with self._echo_lock:
            # These receipts already reached the native DAC queue. Keep their
            # reference across interruption because that acoustic tail still plays.
            self._drain_render_receipts()

    def _process_echo(self, pcm, at):
        with self._echo_lock:
            self._drain_render_receipts()
            return self.echo.process(pcm, at=at)

    async def _ensure_output(self):
        if self.duplex:
            if not self._output_ready or self._output_buffer is None:
                raise RuntimeError("Full duplex requires its started shared stream")
            return self._output_buffer
        if self._output_start_task is None:
            self._output_start_task = asyncio.create_task(self._open_output())
        # A cancellation owns the caller, not a still-opening native stream.
        # The call lifecycle retains and joins this task before closing devices.
        return await asyncio.shield(self._output_start_task)

    async def _open_output(self):
        output = CallbackOutput(
            generation=lambda: self._output_generation,
            current_epoch=lambda epoch: self.epochs is None or self.epochs.is_current(epoch),
            clock=self.clock,
        )
        stream = await self._native(lambda: self._audio().RawOutputStream(
            samplerate=16000, blocksize=320, channels=1, dtype="int16",
            device=self.output_device, callback=output.callback,
        ))
        self._output, self._output_buffer = stream, output
        try:
            await self._native(stream.start)
            self._output_ready = True
        except BaseException:
            await self._close_output()
            raise
        return output

    async def _close_output(self):
        if self.duplex:
            # The shared stream is adopted/aborted/closed exactly once by input
            # lifecycle ownership, including a cancelled constructor/start.
            self._output_ready = False
            self._output = None
            return
        if self._output_start_task is not None and self._output_start_task is not asyncio.current_task():
            await asyncio.gather(self._output_start_task, return_exceptions=True)
        self._output_ready = False
        stream = self._output
        if stream is None:
            return
        try:
            await self._native(stream.abort)
        finally:
            try:
                await self._native(stream.close)
            finally:
                self._output = None

    async def _wait_output(self, output, *, generation=None):
        if output.error is not None:
            raise ValueError(output.error)
        previous, started = output.callback_count, self.clock()
        while generation is not None and output.acknowledged_generation < generation:
            if self.clock() - started >= .100:
                output.stalls += 1
                raise RuntimeError("DEVICE-OUTPUT-CALLBACK-STALLED")
            await asyncio.sleep(.002)
            if output.error is not None:
                raise ValueError(output.error)
        # Async-side reference work is never performed in the native callback.
        if self.duplex:
            await asyncio.to_thread(self._clear_echo)
        elif output.receipts:
            output.receipts.clear()
        return previous

    async def clear(self):
        # No PortAudio API participates in normal interruption. A subsequent
        # callback acknowledges the new generation after handing off only silence.
        self._output_generation += 1
        generation, started = self._output_generation, self.clock()
        if not self.duplex:
            self.capture.mute()
        output = self._output_buffer
        if output is None or self._output is None or not self._output_ready:
            self.last_clear_ms = max(0., (self.clock() - started) * 1000)
            return
        output.clear()
        try:
            await self._wait_output(output, generation=generation)
        finally:
            self.last_clear_ms = max(0., (self.clock() - started) * 1000)

    async def play(self, result: TTSResult, *, epoch=None):
        result_epoch = getattr(result, "turn_epoch", None)
        epoch = self._epoch(result_epoch if epoch is None else epoch)
        if result_epoch is not None and epoch != result_epoch:
            raise asyncio.CancelledError
        generation = self._output_generation
        self._check_output(generation, epoch)
        validate_pcm16(result.audio)
        if (not result.audio or result.format.channels != 1
                or result.format.encoding != "pcm_s16le"
                or result.format.sample_rate_hz not in (8000, 16000)):
            raise ValueError("Speaker requires nonempty mono 8/16 kHz PCM16")
        async with self._play_lock:
            self._check_output(generation, epoch)
            if not self.duplex:
                self.capture.mute()
            output = await self._ensure_output()
            self._check_output(generation, epoch)
            self.first_write_at = output.first_write_at = None
            output.last_end_at = None
            pcm = await asyncio.to_thread(
                PCMResampler(result.format.sample_rate_hz, 16000).feed, result.audio)
            started, last_callback, progressed = self.clock(), output.callback_count, self.clock()
            self._playback_owner = (generation, epoch)
            output.begin_clip(generation=generation, epoch=epoch)
            try:
                for offset in range(0, len(pcm), 640):
                    block = pcm[offset:offset + 640].ljust(640, b"\0")
                    while True:
                        self._check_output(generation, epoch)
                        await self._wait_output(output)
                        if output.callback_count != last_callback:
                            last_callback, progressed = output.callback_count, self.clock()
                        if self.clock() - progressed > .250:
                            output.stalls += 1
                            raise RuntimeError("DEVICE-OUTPUT-CALLBACK-STALLED")
                        if output.enqueue(block, generation=generation, epoch=epoch):
                            break
                        await asyncio.sleep(.002)
                output.finish_clip()
                target_handoff = output.last_enqueued
                while (output.last_handed < target_handoff or output.frames
                       or output.last_end_at is None or self.clock() < output.last_end_at):
                    self._check_output(generation, epoch)
                    await self._wait_output(output)
                    if output.callback_count != last_callback:
                        last_callback, progressed = output.callback_count, self.clock()
                    if self.clock() - progressed > .250:
                        output.stalls += 1
                        raise RuntimeError("DEVICE-OUTPUT-CALLBACK-STALLED")
                    if self.clock() - started > len(pcm) / 32000 + 1.5:
                        raise RuntimeError("Speaker playback deadline exceeded")
                    await asyncio.sleep(.002)
                self._check_output(generation, epoch)
            except BaseException:
                if generation == self._output_generation:
                    self._output_generation += 1
                    output.clear()
                raise
            finally:
                self._playback_owner = None
                self.first_write_at = output.first_write_at
