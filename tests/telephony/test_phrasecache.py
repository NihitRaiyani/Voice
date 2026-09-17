"""The fixed-phrase TTS cache (telephony.phrasecache) — docs/06's design, finally built.

The dangerous edge is not the miss (a miss is live TTS, yesterday's behaviour). It is:

  * a STALE clip speaking retired words — held off by the manifest text check;
  * a hit that skips the TTS frame lifecycle — the 0ce455b0 silent-call bug class, held
    off by serving hits through the same audio-context calls the receive loop makes.
"""

import asyncio
import json

from roma.telephony.phrasecache import (
    MANIFEST_NAME,
    PhraseCache,
    phrase_inventory,
    phrase_key,
    saved_inr,
)

# --- the key ------------------------------------------------------------------------------


def test_the_key_normalizes_whitespace_and_form_not_meaning():
    assert phrase_key("  No-cost EMI   available hai. ") == phrase_key(
        "No-cost EMI available hai."
    )
    assert phrase_key("a") != phrase_key("b")
    assert phrase_key("") == phrase_key(None)


def test_changing_one_word_changes_the_key():
    """docs/06: the cache is versioned by content — edit the line, the old entry is dead."""
    assert phrase_key("Ye detail counsellor visit pe batayenge.") != phrase_key(
        "Ye detail counsellor visit pe bataungi."
    )


# --- the inventory ------------------------------------------------------------------------


def test_the_inventory_carries_the_fixed_lines_and_no_format_strings():
    from roma.controller.confirmguard import SAFE_SIGNOFF_LINE
    from roma.guardrails.lexicon import SUBSTITUTIONS

    inventory = phrase_inventory()
    values = set(inventory.values())
    for line in SUBSTITUTIONS.values():
        assert line in values
    assert SAFE_SIGNOFF_LINE in values
    for text in values:
        assert "{" not in text, (
            f"format string in the inventory can never exact-match: {text!r}"
        )


def test_every_inventory_line_survives_its_own_filter():
    from roma.guardrails import safe_output

    for slug, text in phrase_inventory().items():
        assert safe_output(text) == text, slug


# --- the loader ---------------------------------------------------------------------------


def test_the_shipped_assets_load_and_serve_the_substitutions():
    from roma.guardrails.lexicon import SUBSTITUTIONS, BlockCategory

    cache = PhraseCache()
    assert cache, "no phrase assets — run scripts/warm_phrase_cache.py"
    pcm = cache.lookup(SUBSTITUTIONS[BlockCategory.FEE])
    assert pcm and len(pcm) > 320 and len(pcm) % 2 == 0
    assert cache.lookup("koi bilkul naya sentence jo kabhi render nahi hua") is None


def test_a_stale_clip_is_refused_rather_than_speaking_old_words(tmp_path):
    """The wording changed after the render: the clip must NOT load under the new text's
    key, and must not load under its old text either — it simply drops to live TTS."""
    inventory = phrase_inventory()
    slug, current_text = next(iter(inventory.items()))
    (tmp_path / f"{slug}.ulaw").write_bytes(b"\xff" * 4000)
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps({slug: current_text + " PURANA WORDING"}), encoding="utf-8"
    )
    cache = PhraseCache(assets_dir=tmp_path)
    assert not cache
    assert cache.lookup(current_text) is None


def test_missing_assets_degrade_to_an_empty_cache(tmp_path):
    cache = PhraseCache(assets_dir=tmp_path / "nowhere")
    assert not cache
    assert cache.lookup("anything") is None


# --- the hit path inside the TTS service --------------------------------------------------


class _Secret:
    def get_secret_value(self):
        return "sk-test"


class _Settings:
    sarvam_api_key = _Secret()


class _OneLineCache:
    def __init__(self, text, pcm=b"\x00\x01" * 400):
        self._text, self._pcm = text, pcm

    def __bool__(self):
        return True

    def lookup(self, text):
        return self._pcm if text == self._text else None


def _service(cache):
    from roma.telephony.media import build_tts

    svc = build_tts(_Settings(), cache)
    return svc


def test_a_hit_serves_the_full_frame_lifecycle_and_never_touches_the_socket(monkeypatch):
    """The receive loop appends audio then TTSStopped to the audio context and removes it.
    A cache hit must do EXACTLY that — a raw-audio shortcut that skips the stop frame is
    how call 0ce455b0 went five minutes silent."""
    from pipecat.frames.frames import TTSAudioRawFrame, TTSStoppedFrame

    svc = _service(_OneLineCache("Ye detail counsellor visit pe batayenge."))
    appended, removed = [], []

    async def _append(context_id, frame):
        appended.append((context_id, frame))

    async def _remove(context_id):
        removed.append(context_id)

    async def _noop():
        pass

    monkeypatch.setattr(svc, "audio_context_available", lambda cid: True)
    monkeypatch.setattr(svc, "append_to_audio_context", _append)
    monkeypatch.setattr(svc, "remove_audio_context", _remove)
    monkeypatch.setattr(svc, "stop_ttfb_metrics", _noop)

    async def _connect_boom():
        raise AssertionError("a cache hit must never open the websocket")

    monkeypatch.setattr(svc, "_connect", _connect_boom)

    async def run():
        return [
            f async for f in svc.run_tts("Ye detail counsellor visit pe batayenge.", "ctx1")
        ]

    out = asyncio.run(run())
    assert out == [None]
    assert [type(f) for _, f in appended] == [TTSAudioRawFrame, TTSStoppedFrame]
    assert all(cid == "ctx1" for cid, _ in appended)
    assert appended[1][1].context_id == "ctx1"
    assert removed == ["ctx1"]
    assert svc.phrase_hits == 1 and svc.phrase_chars > 0


def test_a_miss_delegates_to_the_real_synthesis_path(monkeypatch):
    from pipecat.services.sarvam import tts as sarvam_tts

    async def _fake_parent(self, text, context_id):
        yield "PARENT_SENTINEL"

    monkeypatch.setattr(sarvam_tts.SarvamTTSService, "run_tts", _fake_parent)
    svc = _service(_OneLineCache("kuch aur"))

    async def run():
        return [f async for f in svc.run_tts("ye cache mein nahi hai", "ctx1")]

    assert asyncio.run(run()) == ["PARENT_SENTINEL"]
    assert svc.phrase_hits == 0


def test_no_cache_at_all_behaves_like_yesterday(monkeypatch):
    from pipecat.services.sarvam import tts as sarvam_tts

    async def _fake_parent(self, text, context_id):
        yield "PARENT_SENTINEL"

    monkeypatch.setattr(sarvam_tts.SarvamTTSService, "run_tts", _fake_parent)
    svc = _service(None)

    async def run():
        return [f async for f in svc.run_tts("kuch bhi", "ctx1")]

    assert asyncio.run(run()) == ["PARENT_SENTINEL"]


# --- reporting ----------------------------------------------------------------------------


def test_saved_inr_is_proportional_and_zero_for_zero():
    assert saved_inr(0) == 0.0
    assert saved_inr(1000) > 0
    assert abs(saved_inr(2000) - 2 * saved_inr(1000)) < 1e-9
