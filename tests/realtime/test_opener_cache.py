"""RED BY DESIGN — the zero-latency outbound opener is not built yet.

Every test here fails with an assertion naming what is missing. They turn green when the
feature lands; until then they are the specification, in executable form.

## Why the feature

Outbound, Roma's opener is already free of the LLM — `PickupGreeter` speaks it from a
template (`opening.py:406-415`, "spoken from template, no LLM"). But it still goes through
live Bulbul synthesis, and that is the last thing between pickup and the lead hearing a
human voice:

    call e84c2e0a   TTS TTFB 1.314s   TTFA 1.559s
    call 932b6c88   TTS TTFB 0.791s   TTFA 1.042s

The lead has just put a phone to their ear and hears nothing for a second. This session's
call log has repeated hangups attributed to exactly that, and one call the user cut with
"greeting was too late".

Inbound already solves it: `canned.opening_line()` is pre-rendered μ-law played straight at
connect, no synthesis. `media.py` gates that to inbound only —
`opening_already_spoken = ... and not is_outbound` — and the reason is sound: outbound the
CALLEE speaks first, so playing audio at t=0 would talk over their "hello?".

But that reason expires the moment `PickupGreeter` decides to greet. From there, nothing
stops us playing bytes instead of synthesizing them. The bytes can be rendered during the
ring — 5-10s of otherwise idle time, on a call we placed and whose lead record we already
wrote to Redis (`trigger.py:84-87`).

## The contract these tests define

- `roma.repositories.redis.opener_audio.OpenerStore` — `put`/`get`/`aclose`, keyed `roma:opener:{token}`,
  the same shape and TTL as `RedisLeadStore` next to it.
- `PickupGreeter(opening_audio_fn=...)` — returns pre-rendered PCM or None.
- A cache HIT plays audio and touches neither TTS nor the LLM.
- A cache MISS falls back to today's template-text path. Never silence, never the LLM while
  a template exists.
- The guardrail is not bypassed: audio cannot be screened, so `safe_output` must run on the
  TEXT at render time, before it ever becomes bytes.
"""

import asyncio

import fakeredis
from roma.domain.conversation.prompts import opening_line
from roma.domain.safety import safe_output

LEAD_TOKEN = "tok_abc123"

# Roma's real outbound opener — what a cached clip must contain.
OPENER_TEXT = opening_line("Nihit")
# Stand-in for rendered telephony audio: 8kHz mono PCM16, half a second of it.
OPENER_PCM = b"\x00\x01" * 4000


def _client():
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


def _openerstore():
    """The unbuilt module, or None. Imported lazily so absence is an assertion, not a
    collection error that reads as a broken suite."""
    try:
        from roma.repositories.redis import opener_audio as openerstore
    except ImportError:
        return None
    return openerstore


def _require_store():
    mod = _openerstore()
    assert mod is not None, (
        "NOT BUILT: roma/repositories/redis/opener_audio.py — OpenerStore(put/get/aclose) keyed "
        "roma:opener:{token}, mirroring RedisLeadStore. Renders the outbound opener to "
        "telephony PCM during the ring so PickupGreeter can play bytes instead of waiting "
        "~0.8-1.3s on Bulbul."
    )
    return mod


def _greeter(**kwargs):
    """A PickupGreeter with its output captured. `enable_direct_mode` because there is no
    task manager here — the repo pattern from `test_opening_guard.py:28-45`."""
    from roma.realtime.opening import PickupGreeter

    g = PickupGreeter(enable_direct_mode=True, **kwargs)
    g.captured = []

    async def _capture(frame, direction=None):
        g.captured.append(frame)

    g.push_frame = _capture
    return g


def test_the_opener_store_exists_and_round_trips_audio():
    mod = _require_store()

    async def run():
        store = mod.OpenerStore(client=_client())
        await store.put(LEAD_TOKEN, OPENER_PCM)
        return await store.get(LEAD_TOKEN)

    assert asyncio.run(run()) == OPENER_PCM


def test_the_opener_key_is_namespaced_like_every_other_roma_key():
    mod = _require_store()
    assert mod.opener_key(LEAD_TOKEN) == f"roma:opener:{LEAD_TOKEN}"


def test_a_missing_token_reads_as_a_miss_not_an_error():
    """A cold cache must be survivable: the call is already ringing."""
    mod = _require_store()

    async def run():
        store = mod.OpenerStore(client=_client())
        return await store.get("never-written")

    assert asyncio.run(run()) is None


def test_the_entry_carries_a_ttl_so_a_dead_lead_does_not_hold_audio_forever():
    """Same posture as `RedisLeadStore` (LEAD_TTL_SECONDS, 30 min): the clip is only
    useful between dialling and pickup. Un-expiring audio blobs are an unbounded write on
    a Redis that also holds live call state."""
    mod = _require_store()

    async def run():
        client = _client()
        store = mod.OpenerStore(client=client)
        await store.put(LEAD_TOKEN, OPENER_PCM)
        return await client.ttl(mod.opener_key(LEAD_TOKEN))

    ttl = asyncio.run(run())
    assert ttl and ttl > 0, "the opener clip was written with no expiry"


def test_a_truncated_clip_is_treated_as_a_miss_not_played_as_a_burst_of_noise():
    """The failure mode a plain `get` would ship.

    A partial write — process killed mid-render, Redis evicting under memory pressure —
    returns bytes that are not a valid frame count. Playing them puts static on the line at
    the exact moment the lead says "hello". Short reads must fall through to the template.
    """
    mod = _require_store()

    async def run():
        client = _client()
        store = mod.OpenerStore(client=client)
        await client.set(mod.opener_key(LEAD_TOKEN), b"\x00")  # one byte: half a sample
        return await store.get(LEAD_TOKEN)

    assert asyncio.run(run()) is None, (
        "a truncated clip was returned as playable audio; a short read must read as a miss"
    )


def test_a_cache_hit_plays_audio_and_never_reaches_tts_or_the_llm():
    """The whole point: bytes on the wire, no synthesis, no completion."""
    from pipecat.frames.frames import LLMRunFrame, OutputAudioRawFrame, TextFrame

    greeter = _greeter(
        opening_text_fn=lambda: OPENER_TEXT,
        opening_audio_fn=lambda: OPENER_PCM,
    )
    asyncio.run(greeter._greet("test"))

    kinds = [type(f) for f in greeter.captured]
    assert OutputAudioRawFrame in kinds, (
        "NOT BUILT: PickupGreeter(opening_audio_fn=...) — on a cache hit the opener must be "
        "pushed as OutputAudioRawFrame, not synthesized"
    )
    assert TextFrame not in kinds, "a cache hit still sent text to TTS"
    assert LLMRunFrame not in kinds, "a cache hit still asked the LLM for a line"


def test_a_cache_miss_falls_back_to_the_template_text_never_to_silence():
    """The miss path is today's behaviour and must stay exactly that.

    Not the LLM: a template exists, and the two silent calls in `prompts.py:44-48` are why
    the opener does not depend on a completion.
    """
    from pipecat.frames.frames import LLMRunFrame, TextFrame

    greeter = _greeter(opening_text_fn=lambda: OPENER_TEXT, opening_audio_fn=lambda: None)
    asyncio.run(greeter._greet("test"))

    spoken = [f for f in greeter.captured if isinstance(f, TextFrame)]
    assert spoken, "a cache miss produced no greeting at all — that is dead air at pickup"
    assert spoken[0].text == OPENER_TEXT
    assert LLMRunFrame not in [type(f) for f in greeter.captured]


def test_a_broken_cache_read_degrades_to_the_template_rather_than_failing_the_greeting():
    """Redis down, or a corrupt payload, must not cost the greeting."""
    from pipecat.frames.frames import TextFrame

    consulted = []

    def _explode():
        consulted.append(True)
        raise RuntimeError("redis is down")

    greeter = _greeter(opening_text_fn=lambda: OPENER_TEXT, opening_audio_fn=_explode)
    asyncio.run(greeter._greet("test"))

    # Asserted first, and separately: without this the test passes vacuously today, because
    # `PickupGreeter(**kwargs)` absorbs an unknown keyword instead of rejecting it — so
    # "the fallback works" would really mean "the cache was never consulted".
    assert consulted, (
        "NOT BUILT: PickupGreeter never consulted opening_audio_fn — the keyword is being "
        "swallowed by **kwargs, so the opener cache is not wired in at all"
    )
    assert [f for f in greeter.captured if isinstance(f, TextFrame)], (
        "an exception reading the opener cache killed the greeting; it must fall through to "
        "the template path"
    )


def test_the_guardrail_runs_on_the_text_before_it_becomes_bytes():
    """Audio cannot be screened, so the screening must happen at render time.

    A cached clip is the ONE path that can reach the wire without passing the pre-TTS
    filter, because by then it is no longer text. `docs/04` and the roma-guardrail contract
    both say every branch that can emit audio keeps `safe_output` on it — for this branch
    that means rendering `safe_output(text)`, never the raw line.
    """
    mod = _require_store()
    render = getattr(mod, "render_opener_audio", None)
    assert render is not None, (
        "NOT BUILT: openerstore.render_opener_audio(text) — must synthesize "
        "safe_output(text), so a blocked line cannot be baked into a clip that later "
        "bypasses the pre-TTS filter"
    )
    blocked = "Fees sirf 25000 rupees hai."
    assert safe_output(blocked) != blocked, "test premise: this line must be blockable"
    assert render.__doc__ and "safe_output" in render.__doc__, (
        "render_opener_audio must document and apply safe_output on its input"
    )


def test_a_pre_rendered_opener_must_disarm_the_opening_guard():
    """Live regression, call 614ac582 (2026-08-04): greeted, then silent for the whole call.

    `OpeningTurnGuard` opens on `BotStoppedSpeakingFrame`, which only a GENERATED utterance
    emits. A pre-rendered clip is raw `OutputAudioRawFrame` and produces none, so a guard
    left armed never opens and holds every transcript for the rest of the call. The log:

        opening guard: held a transcript (91 chars) — Roma still opening
        opening guard: held a transcript (29 chars) — Roma still opening   <- "बोलिए कुछ"

    `opening.py` documents this exact failure for the inbound canned opener, which is why
    `opener_is_raw_audio` exists. The cached outbound opener is raw audio too and must set
    the same flag — this test is here because reading that comment was not enough.

    IT WAS NOT ENOUGH EITHER. This test passes a literal `True` and so only ever proved the
    guard behaves correctly WHEN TOLD; it could not see that `media.py` never told it, and
    call 0ce455b0 then burned five silent minutes. The wiring itself is pinned by
    `test_media_wiring.py` — a unit test of a processor cannot verify its caller.
    """
    from roma.realtime.opening import OpeningTurnGuard

    guard = OpeningTurnGuard(opener_is_raw_audio=True, enable_direct_mode=True)
    assert guard.opened, (
        "a canned opener must leave the guard OPEN from the start: nothing downstream will "
        "ever emit the BotStoppedSpeakingFrame that would open it later"
    )


def test_the_cached_opener_matches_the_line_roma_would_otherwise_speak():
    """A stale clip is worse than a slow one: it greets the wrong lead by name.

    The cache is keyed per lead token and the opener carries `{name}`, so the render must be
    derived from `opening_line`, not from a fixed string.
    """
    assert "Nihit" in OPENER_TEXT
    assert opening_line(None) != OPENER_TEXT, (
        "the opener is name-dependent, so a per-token cache entry is required — a single "
        "shared clip would greet every lead with the first lead's name"
    )
