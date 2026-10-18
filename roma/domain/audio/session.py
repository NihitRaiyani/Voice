"""One isolated audio lab stream: jitter -> G.711 -> PCM checks -> 16 kHz blocks."""

from dataclasses import dataclass

from roma.domain.audio.g711 import decode, encode
from roma.domain.audio.jitter import AudioPacket, FixedJitterBuffer
from roma.domain.audio.pcm import PCMFramer, validate_pcm16
from roma.domain.audio.resampling import PCMResampler


@dataclass(frozen=True)
class PCMBlock:
    sequence: int
    timestamp_ms: int
    pcm: bytes
    discontinuity: bool
    sample_rate: int = 16000


class TelephoneIngress:
    def __init__(self, *, delay_ms: int = 60) -> None:
        self.jitter = FixedJitterBuffer(delay_ms=delay_ms)
        self.resampler = PCMResampler(8000, 16000)
        self._gap = False
        self.decoded_frames = 0
        self.decoded_bytes = 0

    def receive(self, packet: AudioPacket, *, now_ms: float) -> bool:
        return self.jitter.push(packet, now_ms=now_ms)

    def drain(self, *, now_ms: float) -> tuple[PCMBlock, ...]:
        output = []
        for item in self.jitter.drain(now_ms=now_ms):
            if item.payload is None:
                self._gap = True
                self.resampler.reset()
                continue
            pcm = decode(item.payload)
            validate_pcm16(pcm)
            converted = self.resampler.feed(pcm)
            validate_pcm16(converted)
            output.append(PCMBlock(item.sequence, item.timestamp_ms, converted, self._gap))
            self._gap = False
            self.decoded_frames += 1
            self.decoded_bytes += len(converted)
        return tuple(output)

    def metrics(self):
        return {
            **self.jitter.metrics.snapshot(),
            "decoded_frames": self.decoded_frames,
            "pcm16_bytes": self.decoded_bytes,
            "resampler_delay_ms": self.resampler.delay_ms,
        }


class TelephoneEgress:
    def __init__(self) -> None:
        self.resampler = PCMResampler(16000, 8000)
        self.framer = PCMFramer(8000)

    def feed(self, pcm16k: bytes) -> tuple[bytes, ...]:
        return tuple(encode(frame) for frame in self.framer.feed(self.resampler.feed(pcm16k)))

    def finish(self) -> tuple[bytes, ...]:
        frames = list(self.framer.feed(self.resampler.finish()))
        tail = self.framer.finish(pad=True)
        if tail:
            frames.append(tail)
        return tuple(encode(frame) for frame in frames)

    def reset(self) -> None:
        """Discard old synthesis/filter carry on interruption; never leak it into a new turn."""
        self.resampler.reset()
        self.framer.discard()
