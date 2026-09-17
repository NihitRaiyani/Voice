#!/usr/bin/env python3
"""Render the inbound opener (`canned.OPENING_LINE`) to an 8kHz μ-law asset.

    uv run python scripts/make_opening_clip.py [--force]

Roma is inbound: the caller dialled Weltec and is waiting to hear that it rang through. The
cold first LLM turn measures 3.28s TTFB (CAfe5a00b) plus synthesis, and three seconds of
silence after you ring a business is when a caller says "hello? hello?" and hangs up. So the
opener is rendered ONCE, here, and `telephony/canned.py` only ever reads bytes off disk —
the same argument docs/06 makes for the filler clips, for the same reason.

Rendered in Roma's own voice (bulbul:v3 / ishita / pace 1.05, matching `media.build_tts`).
A different voice on the first line announces itself as a splice before she has said
anything else.

The line goes through the pre-TTS filter before it reaches Sarvam, exactly like every other
canned asset — audio that reaches the wire has passed the docs/04 contract, no exceptions
for being short.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiohttp

# Reused rather than reimplemented: same endpoint, same voice, same trim. Two renderers that
# drift apart would put Roma's opener in a subtly different voice from her fillers.
from make_filler_clips import RATE, render, trim_silence

from roma.config import get_settings
from roma.guardrails import safe_output
from roma.telephony.canned import OPENING_LINE
from roma.telephony.ulaw import pcm16_to_ulaw

ASSET = Path(__file__).resolve().parents[1] / "src/roma/telephony/assets/opening.ulaw"

# The whole point is that it is short. "Hello, Weltec Institute" is about a second; anything
# much past that is Roma talking over a caller who rang to speak.
MAX_REASONABLE_SECS = 2.0


async def main_async(force: bool) -> int:
    if ASSET.exists() and not force:
        print(f"skip {ASSET.name} (exists; --force to re-render)")
        return 0

    if safe_output(OPENING_LINE) != OPENING_LINE:
        print("REFUSING: the pre-TTS filter would alter the opener", file=sys.stderr)
        return 2

    api_key = get_settings().sarvam_api_key.get_secret_value()
    async with aiohttp.ClientSession() as session:
        pcm = trim_silence(await render(session, api_key, OPENING_LINE))

    ASSET.parent.mkdir(parents=True, exist_ok=True)
    ASSET.write_bytes(pcm16_to_ulaw(pcm))
    secs = len(pcm) / 2 / RATE
    print(f"wrote {ASSET.name}  {secs:.2f}s  {OPENING_LINE!r}")
    if secs > MAX_REASONABLE_SECS:
        print(
            f"  WARNING: {secs:.2f}s is long for an opener. The caller rang to say "
            "something; every extra word is time they spend waiting to say it.",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-render even if the asset exists")
    return asyncio.run(main_async(ap.parse_args().force))


if __name__ == "__main__":
    raise SystemExit(main())
