"""The Step 7 tuning knobs (docs/05 config summary, docs/10 Step 7).

docs/05 says every one of these is "a starting value to re-tune on real Twilio audio" —
they came from eight WhatsApp-codec recordings and the live path is 8kHz μ-law. So they
have to be settable from the environment on the production origin. These tests exist because
a knob that *looks* wired but silently falls back to a module constant is worse than an
honest hardcoded value: the env var lies, and the tuning session produces nothing.
"""

import pytest

from roma.telephony import backchannel

REQUIRED_ENV = {
    "TWILIO_FROM_NUMBER": "+16295550100",
    "SARVAM_API_KEY": "sarvam_test",
    "OPENAI_API_KEY": "openai_test",
    "REDIS_URL": "redis://localhost:6379/0",
}


@pytest.fixture
def settings(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    from roma.config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_settings_defaults_match_the_docs05_reference_constants(settings):
    """Two sources of truth for the same numbers, kept honest by this test.

    `backchannel.py` documents docs/05's values next to the lexicons that select between
    them; `Settings` makes them settable. If someone tunes one and forgets the other, a
    fresh deploy silently reverts to the stale number — so the disagreement must fail here
    rather than on a call.
    """
    assert settings.endpoint_terminal_secs == backchannel.ENDPOINT_TERMINAL_SECS
    assert settings.endpoint_default_secs == backchannel.ENDPOINT_DEFAULT_SECS
    assert settings.endpoint_continuation_secs == backchannel.ENDPOINT_CONTINUATION_SECS
    assert settings.backchannel_max_secs == backchannel.BACKCHANNEL_MAX_SECS


def test_defaults_are_the_live_tuned_values(settings):
    """Pinned literally so a drive-by "improvement" to a threshold has to be deliberate.

    Back to docs/05 on 2026-07-31 after call b5273f93 — the lowered values cut Roma off
    mid-sentence. The reasoning lives in `test_backchannel`; keep the two in step.

    `vad_stop_secs` is deliberately NOT moved with them. It does triple duty (endpoint
    floor, Sarvam flush trigger, and the `effective_stt_wait` subtraction in pipecat's base
    class), so it moves one step at a time against measurements — see the
    `AdaptiveEndpointStopStrategy` docstring. It now sits BELOW `endpoint_default_secs`,
    which is what pipecat's "VAD stop_secs differs from the recommended" warning reports on
    every call; that warning is expected, not a regression."""
    assert settings.vad_stop_secs == 0.45
    assert settings.endpoint_terminal_secs == 0.50
    assert settings.endpoint_default_secs == 0.85
    assert settings.endpoint_continuation_secs == 1.30
    assert settings.backchannel_max_secs == 0.60


def test_every_knob_is_settable_from_the_environment(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VAD_STOP_SECS", "0.70")
    monkeypatch.setenv("ENDPOINT_TERMINAL_SECS", "0.40")
    monkeypatch.setenv("ENDPOINT_DEFAULT_SECS", "0.75")
    monkeypatch.setenv("ENDPOINT_CONTINUATION_SECS", "1.10")
    monkeypatch.setenv("BACKCHANNEL_MAX_SECS", "0.55")
    from roma.config import get_settings

    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.vad_stop_secs == 0.70
        assert s.endpoint_terminal_secs == 0.40
        assert s.endpoint_default_secs == 0.75
        assert s.endpoint_continuation_secs == 1.10
        assert s.backchannel_max_secs == 0.55
    finally:
        get_settings.cache_clear()


def test_barge_in_strategies_receive_the_tuned_values(settings):
    """`build_user_params` must pass settings down, not construct with bare defaults."""
    from roma.telephony.media import build_user_params

    settings.backchannel_max_secs = 0.42
    settings.endpoint_default_secs = 0.77
    settings.endpoint_terminal_secs = 0.33
    settings.endpoint_continuation_secs = 1.55

    p = build_user_params(enable_barge_in=True, settings=settings)
    start = p.user_turn_strategies.start[0]
    stop = p.user_turn_strategies.stop[0]

    assert start._backchannel_max_secs == 0.42
    assert stop._default_user_speech_timeout == 0.77
    assert stop._terminal_secs == 0.33
    assert stop._continuation_secs == 1.55


def test_without_settings_the_strategies_keep_the_docs05_defaults():
    """The OFF branch ships without settings, and the strategies must still be coherent."""
    from roma.telephony.media import build_user_params

    stop = build_user_params(enable_barge_in=True).user_turn_strategies.stop[0]
    assert stop._default_user_speech_timeout == backchannel.ENDPOINT_DEFAULT_SECS
    assert stop._terminal_secs == backchannel.ENDPOINT_TERMINAL_SECS
    assert stop._continuation_secs == backchannel.ENDPOINT_CONTINUATION_SECS


def test_endpoint_timeout_for_honours_the_overrides():
    """The adaptive lexicon picks WHICH value; the caller supplies WHAT it is. A tuned
    strategy whose lexicon still returns module constants would be the silent no-op this
    whole change exists to prevent."""
    tuned = {"terminal_secs": 0.31, "default_secs": 0.79, "continuation_secs": 1.51}

    assert backchannel.endpoint_timeout_for("હા", **tuned) == 0.31
    assert backchannel.endpoint_timeout_for("matlab", **tuned) == 1.51
    assert backchannel.endpoint_timeout_for("", **tuned) == 0.79


def test_endpoint_timeout_for_defaults_are_unchanged():
    """Existing callers pass no overrides and must behave exactly as before."""
    assert backchannel.endpoint_timeout_for("હા") == backchannel.ENDPOINT_TERMINAL_SECS
    assert backchannel.endpoint_timeout_for("matlab") == backchannel.ENDPOINT_CONTINUATION_SECS
