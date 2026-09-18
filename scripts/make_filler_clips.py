#!/usr/bin/env python3
"""Render the filler-token clips (docs/05 "Hiding the 850ms", docs/06 filler cache).

    uv run python scripts/make_filler_clips.py

docs/06 is explicit that these are "always cached, never live-generated" — a filler whose
job is to cover LLM latency cannot itself wait on a TTS round trip. So they are rendered
ONCE here, offline, into 8kHz μ-law assets committed next to the consent clip, and
`roma.realtime.filler` only ever reads bytes off disk.

Rendered in Roma's own voice (bulbul:v3 / ishita / pace 1.05, the same live-verified config
as `media.build_tts`) — a filler in a different voice announces itself as a splice.

Every line is run through the pre-TTS filter before it is sent to Sarvam, same as
`canned.py`: audio that reaches the wire has passed the docs/04 contract, no exceptions for
being short.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp
from roma.core.config import get_settings
from roma.domain.safety import safe_output
from roma.realtime.filler import (
    FILLER_LINES,
    HOLDING_LINES,
    OBJECTION_LINES,
    QUESTION_LINES,
    filler_path,
    level_to_target,
)
from roma.realtime.render import RATE, render, trim_silence
from roma.realtime.ulaw import pcm16_to_ulaw


async def main_async(force: bool) -> int:
    settings = get_settings()
    api_key = settings.sarvam_api_key.get_secret_value()

    async with aiohttp.ClientSession() as session:
        lines = {**FILLER_LINES, **QUESTION_LINES, **OBJECTION_LINES, **HOLDING_LINES}
        for name, text in lines.items():
            if safe_output(text) != text:
                print(f"REFUSING {name!r}: the pre-TTS filter would alter it", file=sys.stderr)
                return 2

            out = filler_path(name)
            if out.exists() and not force:
                print(f"skip {out.name} (exists; --force to re-render)")
                continue

            pcm = level_to_target(trim_silence(await render(session, api_key, text)))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(pcm16_to_ulaw(pcm))
            secs = len(pcm) / 2 / RATE
            print(f"wrote {out.name}  {secs:.2f}s  {text!r}")
            if secs > 0.6:
                print(
                    f"  WARNING: {secs:.2f}s is long for a filler — docs/05 says 200-300ms. "
                    "A long filler delays the real answer instead of masking it.",
                    file=sys.stderr,
                )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-render clips that already exist")
    return asyncio.run(main_async(ap.parse_args().force))


if __name__ == "__main__":
    raise SystemExit(main())
