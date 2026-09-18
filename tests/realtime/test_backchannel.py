"""Backchannel lexicon + adaptive endpointing (docs/05 Layers 2 and 3).

Pure predicates, no pipecat. The load-bearing test here is
`test_vad_span_subtracts_confirmation_windows` — the naive span formula fails silently
by disabling the whole guard, so it gets pinned explicitly.
"""

from dataclasses import dataclass

import pytest
from roma.realtime.backchannel import (
    BACKCHANNEL_MAX_SECS,
    BACKCHANNEL_TOKENS,
    ENDPOINT_CONTINUATION_SECS,
    ENDPOINT_DEFAULT_SECS,
    ENDPOINT_TERMINAL_SECS,
    endpoint_timeout_for,
    is_backchannel,
    vad_span_secs,
)


@pytest.mark.parametrize(
    "text",
    [
        "haan",
        "haan haan",
        "hmm",
        "achha",
        "ji",
        "હા",
        "હા જી",
        "હમ્મ",
        "અચ્છા",
        "हाँ",
        "अच्छा",
        "Haan.",
        "  hmm  ",
    ],
)
def test_backchannel_words(text):
    assert is_backchannel(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "હા પણ મોંઘું છે",
        "haan lekin fees zyada hai",
        "મને વિચારવું છે",
        "vadodara",
        "bilkul confirm karo",
        "",
        "   ",
        "...",
        "?!",
    ],
)
def test_not_backchannel(text):
    assert is_backchannel(text) is False


def test_backchannel_disjoint_from_affirmations_where_it_matters():
    """docs/05's backchannel set is NOT the P1 affirmation set.

    `hmm`/`achha` are pure backchannels and are absent from `_AFFIRMATIONS`; the genuine
    turn-takes in `_AFFIRMATIONS` (`bilkul`, `chalega`, `confirm`) must stay OUT of the
    backchannel set or barge-in would swallow a real answer.
    """
    from roma.domain.conversation.turn import _AFFIRMATIONS

    assert "hmm" in BACKCHANNEL_TOKENS and "hmm" not in _AFFIRMATIONS
    assert "achha" in BACKCHANNEL_TOKENS and "achha" not in _AFFIRMATIONS
    for turn_take in ("bilkul", "chalega", "confirm", "confirmed"):
        assert turn_take not in BACKCHANNEL_TOKENS


def test_gujarati_affirmations_are_backchannels_too():
    """`હા`/`હા જી` are both — that is fine, and is why the guard also requires a
    <600ms span AND Roma mid-utterance before it suppresses anything."""
    from roma.domain.conversation.turn import _AFFIRMATIONS

    assert "હા" in BACKCHANNEL_TOKENS and "હા" in _AFFIRMATIONS


@pytest.mark.parametrize(
    "text",
    [
        "haan",
        "theek hai",
        "ઠીક છે",
        "ठीक है",
        "હા",
        "જી",
        "હા જી",
        "vadodara",
        "Vadodara",
        "વડોદરા",
        "ના",
        "nahi",
    ],
)
def test_terminal_answers_shorten(text):
    assert endpoint_timeout_for(text) == ENDPOINT_TERMINAL_SECS


@pytest.mark.parametrize("text", ["2025", "૨૦૨૫", "२०२५"])
def test_digits_are_terminal_including_indic(text):
    """A spoken year comes back as digits — in Gujarati script. `str.isdigit()` accepts
    ૨૦૨૫; `[0-9]` and `int()` do not."""
    assert endpoint_timeout_for(text) == ENDPOINT_TERMINAL_SECS


@pytest.mark.parametrize(
    "text",
    ["matlab", "actually", "મતલબ", "मतलब", "ek minute", "એક મિનિટ", "एक मिनट"],
)
def test_continuation_markers_extend(text):
    assert endpoint_timeout_for(text) == ENDPOINT_CONTINUATION_SECS


def test_haan_to_is_continuation_not_terminal():
    """`હા તો` is the docs/05 example that breaks a terminal-first ordering: `હા` alone
    is terminal, but `હા તો` means the lead is still going. Continuation must win."""
    assert endpoint_timeout_for("હા") == ENDPOINT_TERMINAL_SECS
    assert endpoint_timeout_for("હા તો") == ENDPOINT_CONTINUATION_SECS
    assert endpoint_timeout_for("haan to") == ENDPOINT_CONTINUATION_SECS
    assert endpoint_timeout_for("हाँ तो") == ENDPOINT_CONTINUATION_SECS


def test_bare_particles_do_not_extend():
    """Bare `તો` / `એક` are ubiquitous; if they extended, nearly every turn would sit at
    1300ms."""
    assert endpoint_timeout_for("તો") == ENDPOINT_DEFAULT_SECS
    assert endpoint_timeout_for("ek") == ENDPOINT_DEFAULT_SECS


def test_matches_tail_only():
    """`_text` is cumulative across the turn, so a terminal word early in the utterance
    must not shorten the endpoint once the lead has kept talking."""
    assert endpoint_timeout_for("હા મને વિચારવું છે") == ENDPOINT_DEFAULT_SECS
    assert endpoint_timeout_for("મને વિચારવું છે હા") == ENDPOINT_TERMINAL_SECS


@pytest.mark.parametrize("text", ["", "   ", "...", "kuch samajh nahi aaya matlab kya"])
def test_endpoint_never_raises(text):
    assert isinstance(endpoint_timeout_for(text), float)


def test_endpoint_spans_are_the_live_tuned_numbers():
    """RESTORED to docs/05 (0.5 / 0.85 / 1.3 / 0.6) on 2026-07-31, third revision.

    History, kept so nobody re-litigates it a fourth time. These were docs/05's numbers;
    lowered to 0.3/0.55/1.10/0.8 on 2026-07-27 against "lagging voice" reports, and the
    default trimmed again 0.60 -> 0.55 on 2026-07-30. Both moves treated the same dial as
    the only lever, and both were made without the outbound instrumentation that arrived
    on 2026-07-31.

    Call b5273f93 is why they came back. Shortening the endpoint does not just risk cutting
    a thinking lead off — on this stack it cuts ROMA off. Sarvam's finals land ~1.0s after
    end-of-speech, so at 0.55s she began answering before the transcript of the turn she was
    answering had arrived; that late final then opened a NEW turn and interrupted her 183ms
    into her own first sentence. Three of four interruptions on that call fired 1ms after a
    final, never from VAD.

    The lag those earlier re-tunes chased is real, and 850ms will bring it back. It is
    covered by the cached filler token (docs/05 "Hiding the 850ms"), which is the lever
    docs/05 specifies for exactly this — not by shortening the threshold again."""
    assert (ENDPOINT_TERMINAL_SECS, ENDPOINT_DEFAULT_SECS, ENDPOINT_CONTINUATION_SECS) == (
        0.50,
        0.85,
        1.30,
    )
    assert BACKCHANNEL_MAX_SECS == 0.60


@dataclass
class _Start:
    start_secs: float
    timestamp: float


@dataclass
class _Stop:
    stop_secs: float
    timestamp: float


def test_vad_span_subtracts_confirmation_windows():
    """THE regression guard for this module.

    A 400ms `હા` under our live config (start_secs 0.2, stop_secs 0.85): VAD confirms
    the start 0.2s after speech began and confirms the stop 0.85s after speech ended, so
    the raw timestamps are 0.65s further apart than the speech actually was.

    The naive `stop.timestamp - start.timestamp` returns 1.05 — above the 600ms
    threshold, so the backchannel guard never fires and every echo becomes a barge-in.
    It fails with no error and no crash, which is why this is pinned.
    """
    start = _Start(start_secs=0.2, timestamp=100.2)
    stop = _Stop(stop_secs=0.85, timestamp=101.25)

    assert vad_span_secs(start, stop) == pytest.approx(0.4)
    assert vad_span_secs(start, stop) < BACKCHANNEL_MAX_SECS
    assert stop.timestamp - start.timestamp == pytest.approx(1.05)
    assert stop.timestamp - start.timestamp > BACKCHANNEL_MAX_SECS


def test_vad_span_long_utterance_exceeds_threshold():
    start = _Start(start_secs=0.2, timestamp=100.2)
    stop = _Stop(stop_secs=0.85, timestamp=102.85)
    assert vad_span_secs(start, stop) == pytest.approx(2.0)
    assert vad_span_secs(start, stop) > BACKCHANNEL_MAX_SECS


@pytest.mark.parametrize(
    "start,stop",
    [
        (None, _Stop(stop_secs=0.85, timestamp=101.0)),
        (_Start(start_secs=0.2, timestamp=100.2), None),
        (None, None),
        (object(), object()),
    ],
)
def test_vad_span_none_when_unknown(start, stop):
    """No VAD (offline tests run `build_vad_fn=lambda: None`) => unknown, and the caller
    must not suppress. Never silently drop lead speech."""
    assert vad_span_secs(start, stop) is None


def test_vad_span_clamps_negative_to_zero():
    """Clock skew / out-of-order frames must not produce a negative span that reads as
    'shorter than any threshold' by accident of sign."""
    start = _Start(start_secs=0.2, timestamp=100.2)
    stop = _Stop(stop_secs=0.85, timestamp=100.85)
    assert vad_span_secs(start, stop) == 0.0
