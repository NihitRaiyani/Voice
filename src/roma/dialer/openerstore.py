"""The outbound opener, rendered during the ring so pickup costs no synthesis.

Roma's opener is already free of the LLM — `PickupGreeter` speaks it from a template
(`opening.py`, "spoken from template, no LLM"). What is left is Bulbul, and it is the worst
place in the call to spend a second:

    call e84c2e0a   TTS TTFB 1.314s   TTFA 1.559s
    call 932b6c88   TTS TTFB 0.791s   TTFA 1.042s

The lead has just put a phone to their ear and hears nothing. Two calls in this build were
cut for exactly that, one with the words "greeting was too late".

Inbound already solves it: `canned.opening_line()` is pre-rendered mu-law played at connect.
`media.py` gates that to inbound only, and the reason is sound — outbound the CALLEE speaks
first, so audio at t=0 would talk over their "hello?". But that reason expires the moment
`PickupGreeter` decides to greet. From there, nothing stops us playing bytes.

The bytes are rendered at DIAL time, into the ring: `trigger_outbound_call` already writes
the lead record to Redis before the call is placed, and the carrier then takes seconds to
make the phone ring. That window is free, and it is the only window there is.

## Why the key is per lead token

The opener carries the lead's name (`opening_line("Nihit")`). One shared clip would greet
every lead with the first lead's name — worse than a slow greeting, and unrecoverable.

## Fail towards the slow path, never towards silence

Every read error, short read and miss returns None, which routes `PickupGreeter` back to
today's template-through-TTS path. A cold cache costs the second it costs today; a wrong
one costs the call.
"""

import logging

from roma.dialer.leadstore import LEAD_TTL_SECONDS
from roma.guardrails import safe_output

_log = logging.getLogger("roma.dialer")

# 8kHz mono PCM16 — `canned.SAMPLE_RATE`, the rate the transport plays at.
SAMPLE_RATE = 8000

# 20ms at 8kHz PCM16. Anything shorter is not an opener, it is a fragment of one: a partial
# write (process killed mid-render, eviction under memory pressure) that would reach the
# lead as a burst of static at the exact moment they say "hello".
MIN_CLIP_BYTES = 320


def opener_key(token: str) -> str:
    """`roma:opener:<token>` — namespaced like every other key we own."""
    return f"roma:opener:{token}"


async def render_opener_audio(text: str, *, synth) -> bytes:
    """Synthesize `safe_output(text)` to PCM16, never the raw line.

    The `safe_output` call is not defensive dressing, it is the whole reason this function
    exists rather than callers synthesizing directly. A cached clip is the ONE path that can
    reach the wire without passing the pre-TTS filter, because by the time it is played it
    is no longer text — nothing downstream can screen it. docs/04 and the roma-guardrail
    contract both require `safe_output` on every branch that can emit audio, so for this
    branch the screening has to happen here, before the bytes exist.

    `synth` is injected (an async callable `str -> bytes`) so this module never imports a
    TTS client — the caller owns which voice renders it, and that must be the SAME renderer
    the filler clips use or one call ends up with two Romas in it.
    """
    safe = safe_output(text)
    if safe != text:
        _log.warning("opener text was substituted by the guardrail before rendering")
    return await synth(safe)


class OpenerStore:
    """Token -> pre-rendered opener PCM, TTL-bounded. Mirrors `RedisLeadStore`.

    Pass a `redis_url` (prod) or inject a client (tests use fakeredis). The `redis` import
    is lazy so this package imports cleanly without redis installed.
    """

    def __init__(
        self, redis_url: "str | None" = None, *, client=None, ttl: int = LEAD_TTL_SECONDS
    ) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("OpenerStore needs a redis_url or a client")
            from redis import asyncio as redis_asyncio

            # decode_responses=False: this holds AUDIO. Decoding it as utf-8 would corrupt
            # it, which is the one bug a byte store must not have.
            client = redis_asyncio.from_url(redis_url, decode_responses=False)
        self._client = client
        self._ttl = ttl

    async def put(self, token: str, pcm: bytes) -> None:
        """Store the clip under `token`, expiring with the lead record it belongs to."""
        await self._client.set(opener_key(token), pcm, ex=self._ttl)

    async def get(self, token: "str | None") -> "bytes | None":
        """The clip, or None for a miss, a short read, or anything unreadable.

        A truncated or odd-length payload reads as a MISS rather than as audio: PCM16 is
        two bytes per sample, so an odd length is proof of a partial write, and playing it
        would put static on the line. Falling back to synthesis costs a second; playing
        noise at pickup costs the call.
        """
        if not token:
            return None
        try:
            raw = await self._client.get(opener_key(token))
        except Exception:  # noqa: BLE001 — a cache miss must never end a ringing call
            _log.warning("opener cache read failed; falling back to synthesis")
            return None
        if not raw:
            return None
        if len(raw) < MIN_CLIP_BYTES or len(raw) % 2:
            _log.warning(
                "opener clip for this token is %d bytes — truncated, treating as a miss",
                len(raw),
            )
            return None
        return raw

    async def aclose(self) -> None:
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()


__all__ = [
    "OpenerStore",
    "opener_key",
    "render_opener_audio",
    "MIN_CLIP_BYTES",
    "SAMPLE_RATE",
]
