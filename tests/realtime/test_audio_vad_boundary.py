"""The real VAD state machine consumes clean 16 kHz lab blocks; energy replaces the model."""

import asyncio
import math

from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams, VADState
from roma.domain.audio.g711 import encode
from roma.domain.audio.jitter import AudioPacket
from roma.domain.audio.pcm import pack
from roma.eval.audio import simulate


class EnergyVAD(VADAnalyzer):
    def num_frames_required(self):
        return 512

    def voice_confidence(self, buffer):
        return (
            1.0
            if any(
                abs(int.from_bytes(buffer[i : i + 2], "little", signed=True)) > 1000
                for i in range(0, len(buffer), 2)
            )
            else 0.0
        )


def test_fixed_audio_boundary_drives_predictable_clean_vad_start_and_stop():
    async def run():
        packets = []
        for n in range(50):
            samples = [
                round(10000 * math.sin(2 * math.pi * 1000 * i / 8000)) if 10 <= n < 30 else 0
                for i in range(160)
            ]
            packets.append((float(n * 20), AudioPacket(n + 1, n * 20, encode(pack(samples)))))
        session, blocks = simulate(packets)
        assert session.metrics()["sequence_gaps"] == 0
        vad = EnergyVAD(
            sample_rate=16000,
            params=VADParams(confidence=0.5, min_volume=0, start_secs=0.032, stop_secs=0.064),
        )
        # The processor normally applies StartFrame's rate before the first audio block.
        vad.set_sample_rate(16000)
        try:
            states = [await vad.analyze_audio(block.pcm) for block in blocks]
        finally:
            await vad.cleanup()
        assert VADState.SPEAKING in states and states[-1] == VADState.QUIET
        assert all(state == VADState.QUIET for state in states[:8])

    asyncio.run(run())
