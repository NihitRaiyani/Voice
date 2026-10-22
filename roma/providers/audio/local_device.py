"""Bounded half-duplex PortAudio I/O, independent of speech/business decisions."""

import asyncio
import sys
from dataclasses import replace
from time import perf_counter

from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
from roma.domain.audio.pcm import validate_pcm16
from roma.domain.audio.turn_policy import TurnPolicy
from roma.providers.ai.contracts import TTSResult


class LocalAudioDevice:
    def __init__(self, *, input_device=None, output_device=None, backend=None, policy=None):
        self.input_device, self.output_device = input_device, output_device
        self._backend = backend
        self.policy = policy or TurnPolicy()
        self.first_write_at = None

    def _audio(self):
        if sys.byteorder != "little":
            raise ValueError("Local PCM device requires a little-endian host")
        if self._backend is None:
            import sounddevice

            self._backend = sounddevice
        return self._backend

    async def listen(self, speech, *, max_seconds=30):
        if not 0 < max_seconds <= self.policy.max_utterance_ms / 1000:
            raise ValueError("Capture limit must be in (0,30] seconds")
        started = perf_counter()
        processing_ms = 0.0
        stream = self._audio().RawInputStream(
            samplerate=16000,
            channels=1,
            dtype="int16",
            blocksize=320,
            device=self.input_device,
        )
        read_task = None
        samples_read = 0
        sample_limit = int(max_seconds * 16000)
        read_marks = []

        def stamp(result):
            last = getattr(result, "last_speech_sample", None)
            if last is None:
                return result, {}
            for end, read_at in read_marks:
                if end >= last:
                    speech_end = read_at - (end - last) / 16000
                    return replace(result, speech_end_at=speech_end), {
                        "endpoint_wait_ms": max(0.0, (perf_counter() - speech_end) * 1000 - result.asr_ms)
                    }
            raise ValueError("Invalid last speech frame timing")
        try:
            read_task = asyncio.create_task(asyncio.to_thread(stream.start))
            await asyncio.shield(read_task)
            read_task = None
            async with asyncio.timeout(max_seconds + 120):
                while perf_counter() - started < max_seconds and samples_read < sample_limit:
                    count = min(320, sample_limit - samples_read)
                    read_task = asyncio.create_task(asyncio.to_thread(stream.read, count))
                    block, overflow = await asyncio.shield(read_task)
                    read_task = None
                    if overflow:
                        raise ValueError("Microphone overflow: incomplete utterance discarded")
                    block = bytes(block)
                    validate_pcm16(block)
                    if len(block) != count * 2:
                        raise ValueError("Unexpected microphone frame size")
                    samples_read += count
                    read_marks.append((samples_read, perf_counter()))
                    tick = perf_counter()
                    results = await speech.feed(block)
                    processing_ms += (perf_counter() - tick) * 1000
                    if results:
                        if len(results) != 1:
                            raise ValueError("Only one finalized microphone turn is admitted")
                        result, endpoint_timing = stamp(results[0])
                        return result, {
                            **endpoint_timing,
                            "capture_ms": max(
                                0.0, (perf_counter() - started) * 1000 - result.asr_ms
                            ),
                            "vad_endpoint_ms": max(0.0, processing_ms - result.asr_ms),
                        }
            tick = perf_counter()
            results = await speech.finish(pad_tail=True, endpoint_reason="deadline")
            processing_ms += (perf_counter() - tick) * 1000
            if results:
                if len(results) != 1:
                    raise ValueError("Only one finalized microphone turn is admitted")
                result, endpoint_timing = stamp(results[0])
                if not result.text.strip() and result.error is None:
                    raise VoiceFailure(VoiceErrorCode.CAPTURE_DEADLINE_NO_TRANSCRIPT)
                return result, {
                    **endpoint_timing,
                    "capture_ms": max(0.0, (perf_counter() - started) * 1000 - result.asr_ms),
                    "vad_endpoint_ms": max(0.0, processing_ms - result.asr_ms),
                }
            return None, {
                "capture_ms": (perf_counter() - started) * 1000,
                "vad_endpoint_ms": processing_ms,
            }
        finally:
            # Abort releases an in-flight native read before close; no orphan reader.
            await asyncio.to_thread(stream.abort)
            if read_task is not None:
                await asyncio.gather(read_task, return_exceptions=True)
            await asyncio.to_thread(stream.close)
            speech.reset()

    async def play(self, result: TTSResult):
        validate_pcm16(result.audio)
        if (
            not result.audio
            or result.format.channels != 1
            or result.format.encoding != "pcm_s16le"
        ):
            raise ValueError("Speaker requires nonempty mono PCM16")
        stream = self._audio().RawOutputStream(
            samplerate=result.format.sample_rate_hz,
            channels=1,
            dtype="int16",
            device=self.output_device,
        )
        write_task = None
        self.first_write_at = None

        def write(block):
            if self.first_write_at is None:
                self.first_write_at = perf_counter()
            return stream.write(block)

        try:
            write_task = asyncio.create_task(asyncio.to_thread(stream.start))
            await asyncio.shield(write_task)
            write_task = None
            for offset in range(0, len(result.audio), 3200):
                write_task = asyncio.create_task(
                    asyncio.to_thread(write, result.audio[offset : offset + 3200])
                )
                if await asyncio.shield(write_task):
                    raise ValueError("Speaker underflow")
                write_task = None
            write_task = asyncio.create_task(asyncio.to_thread(stream.stop))
            await asyncio.shield(write_task)
            write_task = None
        finally:
            await asyncio.to_thread(stream.abort)
            if write_task is not None:
                await asyncio.gather(write_task, return_exceptions=True)
            await asyncio.to_thread(stream.close)
