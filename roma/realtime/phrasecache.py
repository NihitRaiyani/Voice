"""The fixed-phrase TTS cache docs/06 specified and nothing had built (docs/06:25-26).

Roma's most-repeated lines are CONSTANTS: the guardrail substitutions, the safe
confirmation/sign-off lines, the off-topic deflection bank. Every one used to take a full
Bulbul round trip (~0.5s TTFB) on every play, on every call — the opener was the only line
with a cache, keyed per lead. This keys per PHRASE, by a hash of the normalized text, so
changing a wording changes the key and a stale clip can never speak retired words.

## Memory-first, mirrored to Redis — why the lookup is not a network call

The cache exists to remove a round trip from the audio path; putting a smaller round trip
(Redis GET) in its place would rebuild the problem it solves at a lower amplitude. The
clips are static assets loaded once at build, so the hot-path lookup is a dict hit. Redis
gets a write-through mirror at startup (`roma:phrase:<sha1>`) so the cache is inspectable
and shared tooling can read it — losing Redis costs nothing here, and a Redis flush is
repaired by the next boot. Miss ⇒ live TTS, the exact behaviour before this existed.

## Where the HIT happens — inside the TTS service, deliberately

`NonBlockingStartSarvamTTS.run_tts` (media.py) checks this cache before touching the
socket and appends the cached frames to the same audio context the receive loop would
have. That preserves the full frame lifecycle — `TTSStarted/Stopped`,
`BotStoppedSpeaking` — which a pipeline-level bypass with raw `OutputAudioRawFrame`s does
not produce. That bug class has a body: call 0ce455b0 ran five silent minutes because a
raw-audio path emitted no `BotStoppedSpeakingFrame`. Do not move the check upstream.

Rendered offline by `scripts/warm_phrase_cache.py` into `assets/phrases/` (μ-law + a
manifest recording the exact text each clip speaks). The loader refuses any clip whose
manifest text no longer matches the live constant — a stale asset degrades to live TTS
rather than speaking old words.
"""

import hashlib
import json
import logging
import unicodedata
from pathlib import Path

from roma.realtime.ulaw import ulaw_to_pcm16

_log = logging.getLogger("roma.realtime")

PHRASE_ASSETS = Path(__file__).parent / "assets" / "phrases"
MANIFEST_NAME = "manifest.json"

# Same integrity floor as the opener store: shorter than 20ms of PCM16 is not audio.
MIN_CLIP_BYTES = 320

# For the teardown ₹ estimate only — spend gating stays OpenAI-only (`roma.domain.costs.spend`).
# Sarvam's published Bulbul price; VERIFY against the dashboard before quoting savings
# anywhere that matters. Chars are counted on the text a hit replaced.
TTS_INR_PER_1K_CHARS = 1.5


def phrase_key(text: str) -> str:
    """Content hash of the normalized phrase (docs/06: change the line, change the key)."""
    folded = " ".join(unicodedata.normalize("NFC", text or "").split())
    return hashlib.sha1(folded.encode("utf-8")).hexdigest()


def phrase_inventory() -> "dict[str, str]":
    """Every cacheable fixed line, slug -> exact text.

    Collected from the modules that OWN the strings — nothing is retyped here, so a wording
    edit propagates and simply invalidates the old clip. Format strings (readback, offers)
    are excluded: their text varies per call and exact-match would never hit.
    """
    from roma.domain.conversation import confirmguard
    from roma.domain.conversation.offtopic import (
        _SLOT_QUESTIONS,
        CONVERGE_FALLBACK,
        CONVERGE_PREFIX,
        DEFLECTIONS,
    )
    from roma.domain.safety.lexicon import HARD_FAIL_LINE, PERMITTED_MONEY_LINE, SUBSTITUTIONS

    inventory: dict[str, str] = {}
    for category, line in SUBSTITUTIONS.items():
        inventory[f"sub_{category.name.lower()}"] = line
    inventory["hard_fail"] = HARD_FAIL_LINE
    inventory["permitted_money"] = PERMITTED_MONEY_LINE
    for name, value in vars(confirmguard).items():
        if name.startswith("SAFE_") and isinstance(value, str) and "{" not in value:
            inventory[name.lower()] = value
    for category, bank in DEFLECTIONS.items():
        for i, line in enumerate(bank):
            inventory[f"deflect_{category.value}_{i}"] = line
    inventory["converge_fallback"] = CONVERGE_FALLBACK
    for slot, question in _SLOT_QUESTIONS.items():
        inventory[f"converge_{slot}"] = CONVERGE_PREFIX + question
    return inventory


class PhraseCache:
    """In-memory phrase->PCM16 map with an optional Redis mirror. Never raises on lookup."""

    def __init__(self, assets_dir: "Path | None" = None) -> None:
        self._clips: dict[str, bytes] = {}
        self.load_assets(assets_dir or PHRASE_ASSETS)

    def load_assets(self, assets_dir: Path) -> int:
        """Load every manifest clip whose text still matches the live constant."""
        manifest_path = assets_dir / MANIFEST_NAME
        if not manifest_path.exists():
            _log.warning(
                "no phrase-cache assets at %s — fixed lines will use live TTS "
                "(run scripts/warm_phrase_cache.py)",
                assets_dir,
            )
            return 0
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            current = phrase_inventory()
        except Exception:  # noqa: BLE001 — a broken manifest degrades to live TTS
            _log.warning("phrase-cache manifest unreadable; fixed lines will use live TTS")
            return 0
        loaded = stale = 0
        for slug, text in manifest.items():
            if current.get(slug) != text:
                stale += 1
                continue
            path = assets_dir / f"{slug}.ulaw"
            try:
                pcm = ulaw_to_pcm16(path.read_bytes())
            except Exception:  # noqa: BLE001 — one bad clip must not empty the cache
                _log.warning("phrase clip %s failed to load; skipping", path.name)
                continue
            if len(pcm) < MIN_CLIP_BYTES or len(pcm) % 2:
                _log.warning("phrase clip %s failed integrity; skipping", path.name)
                continue
            self._clips[phrase_key(text)] = pcm
            loaded += 1
        if stale:
            _log.warning(
                "%d phrase clip(s) are STALE (wording changed since render) — they will use "
                "live TTS until scripts/warm_phrase_cache.py re-renders them",
                stale,
            )
        _log.info("phrase cache loaded: %d clip(s)", loaded)
        return loaded

    def __bool__(self) -> bool:
        return bool(self._clips)

    def lookup(self, text: str) -> "bytes | None":
        """The clip for this exact (normalized) text, or None. One process-wide cache is
        shared by every call, so per-call hit counting lives on the TTS service instance,
        not here."""
        try:
            return self._clips.get(phrase_key(text))
        except Exception:  # noqa: BLE001 — a cache failure is a miss, never a dropped line
            return None

    async def mirror_to_redis(self, redis_url: str) -> None:
        """Write-through mirror, off the hot path. Never raises."""
        try:
            from redis import asyncio as redis_asyncio

            client = redis_asyncio.from_url(redis_url, decode_responses=False)
            try:
                for key, pcm in self._clips.items():
                    await client.set(f"roma:phrase:{key}", pcm)
            finally:
                await client.aclose()
            _log.info("phrase cache mirrored to redis (%d clip(s))", len(self._clips))
        except Exception:  # noqa: BLE001 — the mirror is observability, not the cache
            _log.warning("phrase cache redis mirror failed; in-memory cache unaffected")


def saved_inr(chars: int) -> float:
    """Estimated TTS spend avoided by serving `chars` from cache. Reporting only."""
    return chars / 1000 * TTS_INR_PER_1K_CHARS


__all__ = [
    "PhraseCache",
    "saved_inr",
    "phrase_key",
    "phrase_inventory",
    "PHRASE_ASSETS",
    "MANIFEST_NAME",
    "TTS_INR_PER_1K_CHARS",
]
