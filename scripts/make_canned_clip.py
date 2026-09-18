#!/usr/bin/env python3
"""Produce the 8kHz μ-law canned-audio assets for Step 1 (docs/10).

Two modes:

  # Real: convert an approved recording (must be 8kHz mono 16-bit PCM WAV) -> μ-law
  uv run python scripts/make_canned_clip.py from-wav approved_consent_8k.wav \
      roma/realtime/assets/consent.ulaw

  # Placeholder: synthesize a stand-in clip so the transport is exercisable now
  uv run python scripts/make_canned_clip.py placeholder \
      roma/realtime/assets/consent.ulaw --seconds 1.6

The committed assets are PLACEHOLDERS until Weltec signs off the consent wording and
the approved TEST line is recorded. If your recording isn't already 8kHz/mono/16-bit:
  ffmpeg -i in.wav -ar 8000 -ac 1 -sample_fmt s16 out_8k.wav
"""

import argparse
import math
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roma.realtime.ulaw import pcm16_to_ulaw

RATE = 8000


def _pcm16_from_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(
                f"{path}: need 8000 Hz, mono, 16-bit PCM (got "
                f"{w.getframerate()} Hz, {w.getnchannels()} ch, "
                f"{w.getsampwidth() * 8}-bit). Re-encode with ffmpeg (see header)."
            )
        return w.readframes(w.getnframes())


def _placeholder_pcm(seconds: float) -> bytes:
    """A gentle 3-syllable tone burst — audibly a stand-in, not speech."""
    n = int(RATE * seconds)
    out = bytearray(2 * n)
    for i in range(n):
        t = i / RATE
        burst = int(t / 0.5) % 2 == 0
        env = 0.35 * (1 - math.cos(2 * math.pi * (t % 0.5) / 0.5)) / 2
        s = int(env * 30000 * math.sin(2 * math.pi * 480 * t)) if burst else 0
        s &= 0xFFFF
        out[2 * i] = s & 0xFF
        out[2 * i + 1] = (s >> 8) & 0xFF
    return bytes(out)


def _write(pcm: bytes, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pcm16_to_ulaw(pcm))
    print(f"wrote {out} ({len(pcm) // 2} samples, {len(pcm) // 2 / RATE:.2f}s μ-law)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)

    w = sub.add_parser("from-wav", help="convert an 8kHz mono 16-bit WAV to μ-law")
    w.add_argument("wav", type=Path)
    w.add_argument("out", type=Path)

    p = sub.add_parser("placeholder", help="synthesize a stand-in tone clip")
    p.add_argument("out", type=Path)
    p.add_argument("--seconds", type=float, default=1.6)

    args = ap.parse_args()
    if args.mode == "from-wav":
        _write(_pcm16_from_wav(args.wav), args.out)
    else:
        _write(_placeholder_pcm(args.seconds), args.out)


if __name__ == "__main__":
    main()
