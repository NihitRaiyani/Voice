"""Bounded half-duplex PortAudio I/O, independent of speech/business decisions."""

import asyncio
import sys
from time import perf_counter

from roma.domain.audio.pcm import validate_pcm16
from roma.providers.ai.contracts import TTSResult


class LocalAudioDevice:
    def __init__(self, *, input_device=None, output_device=None, backend=None):
        self.input_device, self.output_device = input_device, output_device
        self._backend = backend

    def _audio(self):
        if sys.byteorder != "little":
            raise ValueError("Local PCM device requires a little-endian host")
        if self._backend is None:
            import sounddevice

            self._backend = sounddevice
        return self._backend

    async def listen(self, speech, *, max_seconds=30):
        if not 0 < max_seconds <= 30:
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
        try:
            read_task = asyncio.create_task(asyncio.to_thread(stream.start))
            await asyncio.shield(read_task)
            read_task = None
            async with asyncio.timeout(max_seconds + 120):
                while perf_counter() - started < max_seconds:
                    read_task = asyncio.create_task(asyncio.to_thread(stream.read, 320))
                    block, overflow = await asyncio.shield(read_task)
                    read_task = None
                    if overflow:
                        raise ValueError("Microphone overflow: incomplete utterance discarded")
                    block = bytes(block)
                    validate_pcm16(block)
                    if len(block) != 640:
                        raise ValueError("Unexpected microphone frame size")
                    tick = perf_counter()
                    results = await speech.feed(block)
                    processing_ms += (perf_counter() - tick) * 1000
                    if results:
                        if len(results) != 1:
                            raise ValueError("Only one finalized microphone turn is admitted")
                        result = results[0]
                        return result, {
                            "capture_ms": max(
                                0.0, (perf_counter() - started) * 1000 - result.asr_ms
                            ),
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
        try:
            write_task = asyncio.create_task(asyncio.to_thread(stream.start))
            await asyncio.shield(write_task)
            write_task = None
            for offset in range(0, len(result.audio), 3200):
                write_task = asyncio.create_task(
                    asyncio.to_thread(stream.write, result.audio[offset : offset + 3200])
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
