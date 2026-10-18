"""Deterministic, synthetic audio lab: no environment, model, carrier or database access."""

import math
from itertools import pairwise

from roma.domain.audio.g711 import encode
from roma.domain.audio.jitter import AudioPacket
from roma.domain.audio.pcm import pack
from roma.domain.audio.session import PCMBlock, TelephoneIngress


def fixtures() -> dict[str, list[tuple[float, AudioPacket]]]:
    clean = []
    for n in range(50):
        samples = [
            round(10000 * math.sin(2 * math.pi * 1000 * (n * 160 + i) / 8000))
            for i in range(160)
        ]
        clean.append((float(n * 20), AudioPacket(n + 1, n * 20, encode(pack(samples)))))
    reordered = list(clean)
    reordered[6], reordered[7] = (clean[7][0], clean[6][1]), (clean[6][0], clean[7][1])
    duplicate = [*clean, (clean[8][0] + 1, clean[8][1])]
    burst = [
        (arrival + (100 if 6 <= i <= 9 else 0), packet)
        for i, (arrival, packet) in enumerate(clean)
    ]
    jitter = [
        (arrival + (0, 12, 3, 25, 7)[i % 5], packet)
        for i, (arrival, packet) in enumerate(clean)
    ]
    return {
        "clean": clean,
        "loss": [p for i, p in enumerate(clean) if i not in (6, 7)],
        "reordering": reordered,
        "duplication": duplicate,
        "burst_delay": burst,
        "variable_jitter": jitter,
    }


def simulate(arrivals: list[tuple[float, AudioPacket]], *, delay_ms: int = 60):
    session = TelephoneIngress(delay_ms=delay_ms)
    arrivals = sorted(arrivals, key=lambda item: item[0])
    emitted: list[PCMBlock] = []
    index = 0
    horizon = int(max((a + delay_ms + 200 for a, _ in arrivals), default=0))
    for tick in range(0, horizon + 1, 5):
        while index < len(arrivals) and arrivals[index][0] <= tick:
            _arrival, packet = arrivals[index]
            # Advance deadlines BEFORE a later arrival; absence of further packets cannot stall playout.
            emitted.extend(session.drain(now_ms=float(tick)))
            session.receive(packet, now_ms=float(tick))
            index += 1
        emitted.extend(session.drain(now_ms=float(tick)))
    return session, tuple(emitted)


def report(*, delay_ms: int = 60) -> dict:
    cases = {}
    for name, arrivals in fixtures().items():
        session, output = simulate(arrivals, delay_ms=delay_ms)
        cases[name] = {
            **session.metrics(),
            "output_frames": len(output),
            "ordered": all(a.sequence < b.sequence for a, b in pairwise(output)),
            "aligned_20ms_pcm": all(len(block.pcm) == 640 for block in output),
            "pending": session.jitter.pending,
        }
    return {
        "schema": "section14-audio-v1",
        "synthetic_tone_only": True,
        "carrier": {"rate": 8000, "codec": "G.711 mu-law", "bytes_per_20ms": 160},
        "model_pcm": {"rate": 16000, "sample_bits": 16, "bytes_per_20ms": 640},
        "fixed_jitter_ms": delay_ms,
        "adaptive_or_plc": False,
        "asr_or_native_quality_measured": False,
        "cases": cases,
    }
