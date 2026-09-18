"""Multi-call isolation (docs/08): per-call observability is keyed by stream, so concurrent
calls never clobber a shared 'last call' slot. The old process-global app.state.last_* is gone.

The full websocket handler can't be driven through Starlette's TestClient (it deadlocks — see
verify_media.py), so these exercise the registry helpers directly plus the app-level init."""

import asyncio
from types import SimpleNamespace

import pytest

from roma.telephony.media import CallHandles, _registered_call, active_calls, sole_call

REQUIRED_ENV = {
    "TWILIO_ACCOUNT_SID": "AC" + "1" * 32,
    "TWILIO_AUTH_TOKEN": "test-auth-token",
    "TWILIO_FROM_NUMBER": "+16295550100",
    "SARVAM_API_KEY": "sarvam_test",
    "OPENAI_API_KEY": "openai_test",
    "REDIS_URL": "redis://localhost:6379/0",
}


def _app(calls):
    return SimpleNamespace(state=SimpleNamespace(calls=calls))


def _handles(tag):
    return CallHandles(counter=tag, transcript=tag, pretts=tag, phase_ctrl=tag)


def test_sole_call_none_when_empty():
    assert sole_call(_app({})) is None


def test_sole_call_returns_the_single_active_call():
    h = _handles("only")
    assert sole_call(_app({"MZ1": h})) is h


def test_two_concurrent_calls_do_not_clobber_each_other():
    a, b = _handles("a"), _handles("b")
    app = _app({"MZ1": a, "MZ2": b})
    assert active_calls(app) == {"MZ1": a, "MZ2": b}
    assert sole_call(app) is None


def test_sole_call_none_when_state_has_no_registry():
    assert sole_call(SimpleNamespace(state=SimpleNamespace())) is None


def test_two_overlapping_registrations_coexist_then_both_deregister():
    app = _app({})

    async def run():
        async def one_call(sid, tag):
            async with _registered_call(app, sid, _handles(tag)):
                await asyncio.sleep(0)
                assert set(active_calls(app)) == {"MZ1", "MZ2"}
                assert active_calls(app)[sid].counter == tag
                await asyncio.sleep(0)

        await asyncio.gather(one_call("MZ1", "a"), one_call("MZ2", "b"))

    asyncio.run(run())
    assert active_calls(app) == {}


def test_registration_removed_even_when_the_call_body_raises():
    app = _app({})

    async def run():
        with pytest.raises(RuntimeError):
            async with _registered_call(app, "MZ1", _handles("a")):
                assert "MZ1" in active_calls(app)
                raise RuntimeError("pipeline blew up")

    asyncio.run(run())
    assert active_calls(app) == {}


def test_build_media_app_starts_with_empty_registry(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    from roma.config import get_settings

    get_settings.cache_clear()
    try:
        from roma.telephony.media import build_media_app

        app = build_media_app(auto_hang_up=False)
        assert app.state.calls == {}
    finally:
        get_settings.cache_clear()


def _build_app(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    from roma.config import get_settings
    from roma.telephony.media import build_media_app

    get_settings.cache_clear()
    try:
        return build_media_app(
            auto_hang_up=False,
            build_stt_fn=lambda _s: None,
            build_vad_fn=lambda _s: None,
            build_llm_fn=lambda _s: None,
            build_tts_fn=lambda _s, _p=None: None,
            build_store_fn=lambda _s: None,
            build_slot_client_fn=lambda _s: None,
        )
    finally:
        get_settings.cache_clear()


def test_the_placeholder_consent_clip_is_not_loaded_and_not_played(monkeypatch):
    """While the wording is `[PENDING WELTEC COMPLIANCE]` the committed `consent.ulaw` is a
    480 Hz tone burst, not speech — `make_canned_clip.py` calls it "audibly a stand-in".

    Playing it disclosed nothing and cost every lead 1.6s of beeping before Roma's first
    word; live feedback on CAfa2a011 was "a very disturbing echo tune". Silence and a tone
    convey the same amount of disclosure. Only one of them makes the lead think the line
    is broken."""
    app = _build_app(monkeypatch)
    assert app.state.consent_line is None


def test_the_signed_off_consent_line_IS_loaded_once_at_app_build(monkeypatch):
    """The moment Weltec signs off the wording, the clip is back on every call — and it is
    loaded HERE. It used to be loaded inside `on_client_connected`, where `canned._load`
    can raise, and pipecat SWALLOWS exceptions out of that handler: the handler simply
    stopped, so neither the consent line nor the `LLMRunFrame` was queued and the call sat
    open in silence until Twilio timed out, logging an ordinary-looking
    `phase=p1_open won=False`."""
    import roma.telephony.media as media_mod
    from roma.telephony import canned

    monkeypatch.setattr(media_mod, "consent_signed_off", lambda: True)
    monkeypatch.setattr(
        canned, "consent_line", lambda: canned.CannedLine(text="signed off", pcm=b"\x00\x01")
    )
    app = _build_app(monkeypatch)
    assert app.state.consent_line.pcm, "consent audio must be decoded at build time"
    assert app.state.consent_line.text


def test_a_bad_consent_asset_fails_at_build_not_on_the_call(monkeypatch):
    """The failure mode that matters when the real Weltec wording lands: a line that trips
    the pre-TTS filter must crash startup, not turn every call into dead air. Skipping the
    load while unsigned must not have skipped this guard along with it."""
    import roma.telephony.media as media_mod
    from roma.telephony import canned

    def _boom():
        raise ValueError("canned line would be altered by the pre-TTS filter")

    monkeypatch.setattr(media_mod, "consent_signed_off", lambda: True)
    monkeypatch.setattr(canned, "consent_line", _boom)
    with pytest.raises(ValueError, match="pre-TTS filter"):
        _build_app(monkeypatch)


def _build_app_with(monkeypatch, **env):
    for k, v in {**REQUIRED_ENV, **env}.items():
        monkeypatch.setenv(k, v)
    from roma.config import get_settings
    from roma.telephony.media import build_media_app

    get_settings.cache_clear()
    try:
        return build_media_app(
            auto_hang_up=False,
            build_stt_fn=lambda _s: None,
            build_vad_fn=lambda _s: None,
            build_llm_fn=lambda _s: None,
            build_tts_fn=lambda _s, _p=None: None,
            build_store_fn=lambda _s: None,
            build_slot_client_fn=lambda _s: None,
        )
    finally:
        get_settings.cache_clear()


def test_the_lead_can_always_interrupt_whatever_barge_in_says(monkeypatch):
    """Live call CA3e7f4c58 (2026-07-27, gpt-4o): "agent is talking in a loop and doesn't
    stop". Roma spoke CONTINUOUSLY from t+88s to t+143s — fifty-six seconds — while the
    lead's channel shows full-amplitude speech at 97, 101, 102, 109, 127, 128, 135, 136,
    138, 139, 141, 142 and 143s, trying to interrupt her the whole time.

    The mechanism is `enable_interruptions=False` on the two start strategies. A lead
    utterance STARTS a user turn — so a new LLM generation fires, and its audio is appended
    to the TTS queue — but broadcasts NO interruption, so nothing ever flushes what is
    already queued. Every interjection therefore adds another full turn to a queue that
    only grows, and Roma runs further and further behind real time. The spend ledger shows
    the generations bunching as it ran away: gaps of 18.1s, then 7.4s, then 2.8s.

    Being interruptible is not a tuning option on a phone call, so it is no longer attached
    to ENABLE_BARGE_IN. That flag now selects the Step 5B turn-taking STRATEGIES
    (backchannel-aware start, adaptive endpoint) and nothing else.
    """
    from roma.telephony.media import build_user_params

    for barge_in in (False, True):
        for strategy in build_user_params(barge_in).user_turn_strategies.start:
            assert strategy._enable_interruptions is True, (
                f"{type(strategy).__name__} cannot interrupt with barge_in={barge_in}: "
                "the lead's speech will queue a reply without flushing Roma's audio"
            )


def test_barge_in_on_is_a_warning_at_build(monkeypatch, caplog):
    """Was `test_barge_in_on_is_an_error_at_build`, asserting an ERROR that said the flag
    fed Roma's voice back into the VAD and produced a screeching echo loop.

    The screech was real — CA4a69d27, painful enough that the lead ended the call — but the
    echo explanation for it was not, and this test helped keep it alive. Measuring the
    inbound leg of all twenty recorded calls settled it: Roma's voice never returns up it,
    on any call, with the flag on or off. Acting on the false claim is what produced the
    CA3e7f4c58 monologue.

    So the level drops to WARNING and the wording changes to what is still defensible: the
    flag selects the least-proven strategies on the audio path, they were live when the
    screech happened, and nobody has explained it yet."""
    import logging

    with caplog.at_level(logging.WARNING, logger="roma.telephony"):
        _build_app_with(monkeypatch, ENABLE_BARGE_IN="true")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("ENABLE_BARGE_IN is ON" in m for m in msgs)
    assert not any("echo" in m and "VAD" in m for m in msgs), (
        "the disproven echo claim is back in the build warning"
    )


def test_barge_in_off_says_nothing(monkeypatch, caplog):
    """The shipping path must stay quiet, or the warning stops being read."""
    import logging

    with caplog.at_level(logging.WARNING, logger="roma.telephony"):
        _build_app_with(monkeypatch, ENABLE_BARGE_IN="false")
    assert not any("ENABLE_BARGE_IN" in r.getMessage() for r in caplog.records)


def test_barge_in_defaults_to_on(monkeypatch):
    """Flipped 2026-07-27, having previously read "the default is the safe value ... `.env`
    overriding it produced the screeching call".

    That framing tied one flag to two unrelated things. Whether the lead can interrupt is
    now unconditional (`test_the_lead_can_always_interrupt_whatever_barge_in_says`); this
    flag only chooses the Step 5B turn-taking strategies, whose endpoint spans have since
    been tuned on live audio."""
    from roma.config import Settings

    assert Settings.model_fields["enable_barge_in"].default is True


def test_building_the_app_without_configured_logging_says_so(monkeypatch, capsys):
    """The failure this catches lies to you rather than breaking.

    Without `configure_logging()` the last-resort handler emits WARNING and above only, so
    every `roma.telephony` INFO line vanishes — including `media stream started`, which the
    runbook treats as the binary proof that a call's socket arrived. Boot the bare factory,
    ring the number, and the call connects, builds its pipeline, opens Sarvam STT and TTS and
    spends credit while the log shows nothing at all. Measured exactly that way on
    2026-07-31, and an hour went into debugging the tunnel."""
    import logging

    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    from roma.config import get_settings

    get_settings.cache_clear()
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []  # no configure_logging() has run
    try:
        from roma.telephony.media import build_media_app

        build_media_app(auto_hang_up=False)
        assert "logging is not configured" in capsys.readouterr().err
    finally:
        root.handlers = saved
        get_settings.cache_clear()
