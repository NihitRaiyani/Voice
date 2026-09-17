"""Render Roma's fixed lines into the phrase cache (docs/06 fixed-phrase cache).

    uv run python scripts/warm_phrase_cache.py            # render missing/stale clips
    uv run python scripts/warm_phrase_cache.py --force    # re-render everything

Writes `src/roma/telephony/assets/phrases/<slug>.ulaw` plus `manifest.json` mapping each
slug to the EXACT text it speaks. The loader (`telephony.phrasecache`) refuses any clip
whose manifest text no longer matches the live constant, so editing a wording anywhere
just invalidates its clip until this script runs again — a stale clip can never speak
retired words.

Same render pipeline as `make_filler_clips.py`: Bulbul v3 → trim silence → level to the
filler RMS, and every line must survive `safe_output` unaltered before it is rendered —
a cached line that the filter would rewrite live is a config bug, not an asset.
"""

import argparse
import asyncio
import json
import sys

import aiohttp

from roma.config import get_settings
from roma.guardrails import safe_output
from roma.telephony.filler import SENTENCE_TARGET_RMS, level_to_target
from roma.telephony.phrasecache import MANIFEST_NAME, PHRASE_ASSETS, phrase_inventory
from roma.telephony.render import RATE, render, trim_silence
from roma.telephony.ulaw import pcm16_to_ulaw


async def main_async(force: bool) -> int:
    settings = get_settings()
    api_key = settings.sarvam_api_key.get_secret_value()

    inventory = phrase_inventory()
    manifest_path = PHRASE_ASSETS / MANIFEST_NAME
    manifest: dict = {}
    if manifest_path.exists() and not force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    async with aiohttp.ClientSession() as session:
        for slug, text in inventory.items():
            if safe_output(text) != text:
                print(f"REFUSING {slug!r}: the pre-TTS filter would alter it", file=sys.stderr)
                return 2

            out = PHRASE_ASSETS / f"{slug}.ulaw"
            if out.exists() and manifest.get(slug) == text and not force:
                print(f"skip {out.name} (current)")
                continue

            # SENTENCE level, not the filler level: these play in place of Roma's own
            # speech, so a cached line at the quieter filler RMS changed volume mid-call.
            pcm = level_to_target(
                trim_silence(await render(session, api_key, text)), SENTENCE_TARGET_RMS
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(pcm16_to_ulaw(pcm))
            manifest[slug] = text
            print(f"wrote {out.name}  {len(pcm) / 2 / RATE:.2f}s  {text!r}")

    # Drop manifest entries whose constant no longer exists, and their clips.
    for slug in sorted(set(manifest) - set(inventory)):
        (PHRASE_ASSETS / f"{slug}.ulaw").unlink(missing_ok=True)
        del manifest[slug]
        print(f"pruned {slug} (no longer in the inventory)")

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"manifest: {len(manifest)} clip(s)")

    # Mirror to Redis for inspectability (`roma:phrase:<sha1>`). The server never reads
    # this — its cache is in-memory from the assets above — so a missing Redis is a shrug.
    from roma.telephony.phrasecache import PhraseCache

    await PhraseCache().mirror_to_redis(settings.redis_url.get_secret_value())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-render clips that already exist")
    args = ap.parse_args()
    return asyncio.run(main_async(args.force))


if __name__ == "__main__":
    raise SystemExit(main())
