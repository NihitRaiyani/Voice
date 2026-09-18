"""Step 2 wiring: Silero VAD + Sarvam Saaras STT factory config.

These pin the locked contract (docs/01: Saaras, code-mix; docs/05: 850ms turn-final).
The transcript data-flow is covered by test_transcript.py via Pipecat's run_test
harness; end-to-end transcripts on a live 8kHz call are the Step-2 measurement gate
(the full websocket pipeline can't be driven through Starlette's TestClient — 3+
async processors deadlock its synchronous portal at teardown; real uvicorn is fine).
"""

import pytest
from pipecat.audio.vad.silero import SileroVADAnalyzer

REQUIRED_ENV = {
    "TWILIO_ACCOUNT_SID": "AC" + "1" * 32,
    "TWILIO_AUTH_TOKEN": "test-auth-token",
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


def test_build_vad_uses_turn_final_stop_secs(settings):
    from roma.telephony.media import VAD_STOP_SECS, build_vad

    vad = build_vad(settings)
    assert isinstance(vad, SileroVADAnalyzer)
    # Retuned 0.75 -> 0.45 on 2026-08-04 to return ~288ms per turn; the saving and the
    # 448-736ms risk window it opens are pinned in `test_vad_endpoint.py`.
    assert VAD_STOP_SECS == 0.45
    assert vad.params.stop_secs == 0.45
    assert settings.vad_stop_secs == VAD_STOP_SECS


def test_build_vad_reads_stop_secs_from_settings(settings):
    """THE Step-7 knob. If this ever reverts to the module constant, tuning 850ms on the
    production origin silently becomes a code edit again and the env var lies."""
    from roma.telephony.media import build_vad

    settings.vad_stop_secs = 0.62
    assert build_vad(settings).params.stop_secs == 0.62


def test_build_stt_configures_saaras_codemix_autodetect(settings):
    """The language assertion is REVERSED from `gu-IN`, deliberately — see
    `test_the_stt_language_is_auto_detect_not_a_pinned_language` for the live evidence.

    D3's reasoning (Vadodara leads are Gujarati-primary) was correct and its conclusion was
    not: they code-switch every other clause, which is what `codemix` mode exists for, and
    pinning the decoder to one language fights the mode.
    """
    from roma.telephony.media import build_stt

    stt = build_stt(settings)
    assert stt._settings.model == "saaras:v3"
    assert stt._mode == "codemix"
    assert stt._settings.language is None


def test_barge_in_off_still_lets_the_lead_interrupt_and_keeps_endpointing():
    """Inverted on 2026-07-27, against this test's own explicit instruction not to ("Do not
    invert it when 5B lands"). The instruction is overridden on evidence, so here is the
    evidence.

    The old assertion rested on interruptions firing on "every user utterance / echo". Those
    are two different things and only one was ever unwanted:

    - The ECHO half is false. Roma's voice does not return up the inbound leg on any of the
      twenty recorded calls, flag on or off — the carrier suppresses it. There was nothing
      to fire on.
    - The USER-UTTERANCE half is the behaviour a phone call requires. Cancelling Roma's TTS
      when the lead starts talking is not choppy audio, it is listening.

    Keeping interruptions off cost CA3e7f4c58: fifty-six unbroken seconds of Roma talking
    while the lead tried to break in twelve times. `enable_interruptions=False` still lets a
    lead utterance START a turn, so each interjection queued a whole new generation behind
    audio nothing would ever flush.

    The STOP strategy assertion is unchanged and still matters."""
    from roma.telephony.media import build_user_params

    p = build_user_params()
    starts = p.user_turn_strategies.start
    assert starts, "expected user-turn start strategies"
    assert all(s._enable_interruptions is True for s in starts)
    assert p.user_turn_strategies.stop, "endpointing (stop strategy) must remain"


def test_barge_in_defaults_on():
    """Flipped 2026-07-27. The old reason for defaulting off — "no clean audio path ...
    ships barge-in to a laptop with no AEC" — assumed an echo that measurement could not
    find on any of the twenty recorded calls.

    The flag no longer gates whether the lead can interrupt (that is unconditional, see
    `test_barge_in_off_still_lets_the_lead_interrupt_and_keeps_endpointing`). It selects the
    Step 5B turn-taking strategies, and those are on by default now that the endpoint spans
    they depend on have been tuned against live audio."""
    from roma.config import Settings

    assert Settings.model_fields["enable_barge_in"].default is True


def test_barge_in_off_keeps_pipecat_smart_turn():
    """INVERTED on 2026-07-31. This used to assert the adaptive strategy must NOT survive
    `enable_barge_in=False`, on the reasoning that it was Step 5B machinery that should not
    leak onto the shipping path. That conflated two of docs/05's three layers.

    Endpointing (Layer 2) answers "has the LEAD finished speaking?". Barge-in (Layer 3)
    answers "may Roma be cut off?". Only the second is what the flag owns, via
    `enable_interruptions` on the START strategies. Under the old wiring, turning barge-in
    off silently reverted every tuned endpoint value to a flat `VAD_STOP_SECS` and nothing
    in the log said so — the same failure shape as the dropped `vad_analyzer`, the dead
    watchdog disarm, and the `OpeningTurnGuard` that never opened.

    So: the adaptive stop strategy is now required in BOTH branches, and the flag must
    still govern interruptions."""
    from roma.telephony.media import build_user_params
    from roma.telephony.turntaking import AdaptiveEndpointStopStrategy

    off = build_user_params(enable_barge_in=False).user_turn_strategies
    on = build_user_params(enable_barge_in=True).user_turn_strategies

    assert any(isinstance(s, AdaptiveEndpointStopStrategy) for s in off.stop)
    assert any(isinstance(s, AdaptiveEndpointStopStrategy) for s in on.stop)

    # The endpoint span survives the flag identically -- that is the whole point.
    (off_stop,) = [s for s in off.stop if isinstance(s, AdaptiveEndpointStopStrategy)]
    (on_stop,) = [s for s in on.stop if isinstance(s, AdaptiveEndpointStopStrategy)]
    assert off_stop._user_speech_timeout == on_stop._user_speech_timeout

    # ...and what the flag DOES own is still switched: the backchannel guard is a start
    # strategy and belongs only to the barge-in path.
    from roma.telephony.turntaking import BackchannelAwareUserTurnStartStrategy

    assert not any(isinstance(s, BackchannelAwareUserTurnStartStrategy) for s in off.start)
    assert any(isinstance(s, BackchannelAwareUserTurnStartStrategy) for s in on.start)


def test_barge_in_on_installs_the_backchannel_guard_alone():
    """ON: exactly one start strategy, with interruptions enabled.

    `VADUserTurnStartStrategy` must be GONE, not merely reordered.
    `UserTurnController._trigger_user_turn_start` discards a second start in the same
    turn, and VAD fires at speech onset before any transcript exists — so leaving it in
    would silently pre-empt the guard on every turn while everything still looked wired.
    """
    from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
        TranscriptionUserTurnStartStrategy,
    )
    from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy

    from roma.telephony.media import build_user_params
    from roma.telephony.turntaking import (
        AdaptiveEndpointStopStrategy,
        BackchannelAwareUserTurnStartStrategy,
    )

    p = build_user_params(enable_barge_in=True)
    starts = p.user_turn_strategies.start
    assert len(starts) == 1
    assert isinstance(starts[0], BackchannelAwareUserTurnStartStrategy)
    assert starts[0]._enable_interruptions is True
    assert not any(
        isinstance(s, (VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy))
        for s in starts
    )

    stops = p.user_turn_strategies.stop
    assert len(stops) == 1
    assert isinstance(stops[0], AdaptiveEndpointStopStrategy)


def test_build_tts_pins_the_live_verified_voice_config(settings):
    """TTS config has regressed on a live call before — `bulbul:v2` rendered the Hindi/
    English switch as garbled audio, which is why v3 is locked. Pin the whole shape.

    v3 supports `pace` (0.5-2.0) and `temperature`; it does NOT support `pitch` or
    `loudness` (v2-only), so `pace` is the single prosody dial.
    """
    from roma.telephony.media import TTS_PACE, TTS_VOICE, build_tts

    s = build_tts(settings)._settings
    assert s.model == "bulbul:v3"
    assert s.voice == TTS_VOICE == "ishita"
    assert s.pace == TTS_PACE == 1.05
    assert s.language == "hi-IN"
    assert s.enable_preprocessing is True


def test_tts_voice_is_a_real_bulbul_v3_speaker():
    """A typo'd speaker name is a runtime failure on a live call, not an import error."""
    from pipecat.services.sarvam.tts import SarvamTTSSpeakerV3

    from roma.telephony.media import TTS_VOICE

    assert TTS_VOICE in {s.value for s in SarvamTTSSpeakerV3}


def _metrics_frame(pairs):
    from pipecat.frames.frames import MetricsFrame
    from pipecat.metrics.metrics import TTFBMetricsData

    return MetricsFrame(data=[TTFBMetricsData(processor=p, value=v) for p, v in pairs])


def _usage_logger():
    from roma.telephony.media import _UsageLogger

    return _UsageLogger(enable_direct_mode=True)


def _drive(proc, frames):
    import asyncio

    from pipecat.processors.frame_processor import FrameDirection

    async def _noop(frame, direction=FrameDirection.DOWNSTREAM):
        return None

    proc.push_frame = _noop

    async def run():
        for f in frames:
            await proc.process_frame(f, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return proc


def test_ttfb_is_captured_per_processor():
    u = _drive(_usage_logger(), [_metrics_frame([("OpenAILLMService#0", 1.21)])])
    assert u.ttfb["OpenAILLMService#0"] == [1.21]


def test_ttfb_summary_reports_n_and_p50_per_stage():
    u = _drive(
        _usage_logger(),
        [
            _metrics_frame([("OpenAILLMService#0", 1.0), ("SarvamTTSService#0", 0.4)]),
            _metrics_frame([("OpenAILLMService#0", 1.4)]),
            _metrics_frame([("OpenAILLMService#0", 1.2)]),
        ],
    )
    s = u.ttfb_summary()
    assert s["OpenAILLMService#0"] == {"n": 3, "p50": 1.2, "max": 1.4}
    assert s["SarvamTTSService#0"] == {"n": 1, "p50": 0.4, "max": 0.4}


def test_turn_latency_is_not_measured_here():
    """It used to be, and it could never have worked: this processor sits downstream of
    `LLMUserAggregator`, which CONSUMES the finalized `TranscriptionFrame` rather than
    forwarding it (`llm_response_universal.py`). The anchor never arrived, so the metric
    would have reported an empty dict for the whole call and read as "nothing happened".

    It lives in `TurnFlight` now, written across the two processors that see each end.
    Pinned so nobody re-adds it here."""
    assert not hasattr(_usage_logger(), "turn_latency_summary")


def test_ttfb_summary_is_empty_when_nothing_was_measured():
    """An honest nothing. A zero here would read as 'instant' and send tuning the wrong
    way — which is exactly how the previous instrument failed silently."""
    assert _usage_logger().ttfb_summary() == {}


def test_the_llm_dominates_the_endpoint_wait_on_real_numbers():
    """Documents the live finding (call CA9c5f7cf): LLM TTFB 1.21s against an 850ms
    endpoint wait. If this ever inverts, lowering `vad_stop_secs` becomes worth doing —
    until then it is not, and this test is where that reasoning is written down."""
    from roma.config import Settings

    u = _drive(_usage_logger(), [_metrics_frame([("OpenAILLMService#0", 1.21)])])
    llm_p50 = u.ttfb_summary()["OpenAILLMService#0"]["p50"]
    assert llm_p50 > Settings.model_fields["vad_stop_secs"].default


def test_build_stt_leaves_vad_signals_unset_so_the_flush_signal_stays_on(settings):
    """Sarvam flushes on `VADUserStoppedSpeakingFrame` only when it connected with
    `flush_signal="true"`, and it sets that ONLY when `vad_signals` is falsy
    (`sarvam/stt.py:631`). Setting `vad_signals=True` would look like "use our VAD" and
    would instead drop the flush and hand endpointing back to Sarvam's server side — the
    1.17s p99 path the pipeline VAD exists to replace.

    Pinned because it is a one-word change with no local symptom: the call still works,
    just slower, which is how it would survive review."""
    from roma.telephony.media import build_stt

    stt = build_stt(settings)
    assert stt._settings.vad_signals is None


def test_the_stt_language_is_auto_detect_not_a_pinned_language():
    """THE regression, live call CA1652a5e (2026-07-27).

    `STT_LANGUAGE` was pinned to `gu-IN`, so every Hindi clause on a code-mix call was
    forced through a Gujarati decoder: `હા હવે વાત કર સકતે હૈ` is "kar sakte hain" — Hindi —
    rendered in Gujarati script. Roma spent the middle of that call answering mush.

    `saaras:v3` supports auto-detect, so this was a configuration mistake and not the WER
    ceiling it looked like. Asserted through the SERVICE's own resolution rather than
    against the constant, because `None` meaning "auto-detect" is a fact about Sarvam, not
    about our module — and a pipecat release that changed the default would otherwise
    silently turn this back into a pinned language.
    """
    from pipecat.services.sarvam.stt import SarvamSTTService

    from roma.telephony.media import STT_LANGUAGE, STT_MODEL

    assert STT_LANGUAGE is None
    svc = SarvamSTTService(
        api_key="test-key",
        settings=SarvamSTTService.Settings(model=STT_MODEL, language=STT_LANGUAGE),
    )
    assert svc._get_language_string() == "unknown"


def test_code_mix_mode_survives_the_language_change():
    """Auto-detect is per utterance; `codemix` is what keeps a single sentence mixed rather
    than translated. Losing it would trade one transcription bug for another."""
    from roma.telephony.media import STT_MODE

    assert STT_MODE == "codemix"


def test_the_usage_logger_sits_after_the_tts_service():
    """Live call CA3c7d3c7b reported `SarvamTTSService#0: {'n': 1, 'p50': 0.0}` in the
    teardown while pipecat's own DEBUG lines showed real samples of 1.077s and 1.148s across
    fourteen spoken lines. The largest stage in the latency budget was unmeasured, and the
    number it printed instead read as "instant".

    `FrameProcessor.stop_ttfb_metrics` does `await self.push_frame(frame)`, which is
    DOWNSTREAM by default. `usage` sat between `llm` and `tts`, so LLM and STT metrics
    (emitted upstream of it) arrived and TTS metrics — emitted downstream — never could.

    This is the same class of bug as the dead VAD that survived four calls: an instrument
    reporting configuration where evidence was expected. Position is the whole fix, so
    position is what this pins."""
    import inspect

    from roma.telephony import media

    src = inspect.getsource(media)
    llm_idx = src.index("                    llm,\n")
    tts_idx = src.index("                    tts,\n")
    usage_idx = src.index("                    usage,\n")
    assert llm_idx < tts_idx < usage_idx, (
        "usage must sit downstream of tts or TTS TTFB is never recorded"
    )


def test_a_swallowed_exception_is_counted():
    """`service_errors` only ever counted ErrorFrames reaching the pipeline handler, so every
    `except: log and continue` in the repo was invisible to it. A call whose phase advance
    threw on every turn, whose recording never opened and whose ledger never wrote still
    reported `service_errors=0` — which is exactly how the dead VAD and the silently-unplayed
    filler each survived multiple live calls."""
    from roma.telephony.health import CallHealth

    h = CallHealth()
    assert h.degraded == {}
    h.degrade("phase_advance")
    h.degrade("phase_advance")
    h.degrade("filler_emit")
    assert h.degraded == {"phase_advance": 2, "filler_emit": 1}
    assert h.deaf is False, "a degraded call is not a deaf call"


def test_the_conversational_llm_caps_its_first_attempt_and_retries():
    """Live call acf8e78f (2026-08-01) was SILENT. The lead answered, said "Hello", and heard
    nothing before hanging up at thirty seconds:

        OpenAILLMService#0 TTFB: 63.978s

    One request stalled and nothing capped it. `build_slot_client` has carried explicit
    connect/read timeouts all along, so the throwaway extraction call was protected while the
    one Roma actually speaks with was not — this pins that asymmetry closed.

    Pipecat defaults `retry_on_timeout` to False, so this is opt-in and must stay opted in."""
    from roma.config import Settings
    from roma.telephony.media import LLM_FIRST_ATTEMPT_TIMEOUT_SECS, build_llm

    llm = build_llm(
        Settings(
            sarvam_api_key="x",
            openai_api_key="x",
            redis_url="redis://localhost:6379",
            twilio_from_number="+16295550100",
        )
    )
    assert llm._retry_on_timeout is True, "a stalled completion would hang the call again"
    assert llm._retry_timeout_secs == LLM_FIRST_ATTEMPT_TIMEOUT_SECS
    # Well clear of a merely slow turn: live TTFB runs 0.8-3.0s on this path.
    assert LLM_FIRST_ATTEMPT_TIMEOUT_SECS >= 4.0


def test_stt_connect_does_not_block_the_pipeline_start():
    """THE late greeting. Pipecat forwards StartFrame only after `start()` returns, and
    `SarvamSTTService.start` awaits its websocket connect — so nothing downstream, TTS
    included, could emit audio until Sarvam was up.

    Live call d9ff7ff5 (2026-08-02): stream open at 09:24:29.972, greeter fired correctly at
    09:24:30.495, StartFrame reached the end of the pipeline at 09:24:42.531. Twelve seconds
    of silence, none of it Roma's. It was 3.4s on the call before.

    Safe because STT is not needed until the lead speaks, which is necessarily after Roma has
    greeted them."""
    import asyncio

    from roma.config import Settings
    from roma.telephony.media import NonBlockingStartSarvamSTT, build_stt

    stt = build_stt(
        Settings(
            sarvam_api_key="x",
            openai_api_key="x",
            redis_url="redis://localhost:6379",
            twilio_from_number="+16295550100",
        )
    )
    assert isinstance(stt, NonBlockingStartSarvamSTT)

    connected = asyncio.Event()

    async def _slow_connect():
        await asyncio.sleep(5)
        connected.set()

    scheduled = []
    stt._connect = _slow_connect
    stt.create_task = lambda coro: scheduled.append(coro) or coro.close()

    async def _run():
        from pipecat.frames.frames import StartFrame

        await asyncio.wait_for(stt.start(StartFrame()), timeout=1.0)

    asyncio.run(_run())
    assert scheduled, "connect was not scheduled; StartFrame would still be blocked"
    assert not connected.is_set(), "start() awaited the connect and blocked the pipeline"


def test_a_dropped_stt_handshake_is_retried_before_the_call_is_declared_deaf():
    """This link drops roughly one Sarvam handshake in six ("timed out during opening
    handshake"). A single failed attempt sets `health.deaf`, which makes `CallCloser` hang up
    on a lead who is still talking — that is how calls c5672a23 and dd8a351b ended.

    The retry is only affordable BECAUSE the connect is off the critical path now; blocking
    the pipeline for three attempts would be worse than the failure it fixes."""
    import asyncio

    from roma.telephony.media import STT_CONNECT_ATTEMPTS, NonBlockingStartSarvamSTT

    assert STT_CONNECT_ATTEMPTS >= 2

    stt = NonBlockingStartSarvamSTT.__new__(NonBlockingStartSarvamSTT)
    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("timed out during opening handshake")

    stt._connect = _flaky
    import roma.telephony.media as media_mod

    orig = media_mod.STT_CONNECT_RETRY_SECS
    media_mod.STT_CONNECT_RETRY_SECS = 0.01
    try:
        asyncio.run(stt._connect_with_retry())
    finally:
        media_mod.STT_CONNECT_RETRY_SECS = orig
    assert calls["n"] == 3, "gave up before the handshake would have succeeded"


def test_the_tts_connect_does_not_block_the_pipeline_start_either():
    """Call 16835e83, cut by the lead for a late greeting. Taking the STT connect off the
    startup path left the same bug one processor downstream: `SarvamTTSService.start` awaits
    `_connect()`, so StartFrame could not finish traversing the pipeline until Bulbul's
    socket was up — and the greeter's TextFrame queues behind StartFrame.

        19:58:09.564  STT connect begins
        19:58:11.114  STT connected            (background)
        19:58:13.082  TTS websocket connected  (ON the startup path, +1.97s)
        19:58:13.116  greeting finally generated

    Safe because `run_tts` opens by reconnecting if the socket is closed."""
    import asyncio

    from roma.config import Settings
    from roma.telephony.media import NonBlockingStartSarvamTTS, build_tts

    tts = build_tts(
        Settings(
            sarvam_api_key="x",
            openai_api_key="x",
            redis_url="redis://localhost:6379",
            twilio_from_number="+16295550100",
        )
    )
    assert isinstance(tts, NonBlockingStartSarvamTTS)

    connected = asyncio.Event()

    async def _slow_connect():
        await asyncio.sleep(5)
        connected.set()

    scheduled = []
    tts._connect = _slow_connect
    tts.create_task = lambda coro: scheduled.append(coro) or coro.close()

    async def _run():
        from pipecat.frames.frames import StartFrame

        await asyncio.wait_for(tts.start(StartFrame()), timeout=1.0)

    asyncio.run(_run())
    assert scheduled, "connect was not scheduled; StartFrame would still be blocked"
    assert not connected.is_set(), "start() awaited the connect and blocked the pipeline"


def test_the_tts_still_sets_the_sample_rate_its_config_message_needs():
    """`start()` skips SarvamTTSService.start, which is the only place `_speech_sample_rate`
    is set — and `_send_config` reads it. Dropping it would ship a broken config on connect."""
    import asyncio

    from roma.config import Settings
    from roma.telephony.media import build_tts

    tts = build_tts(
        Settings(
            sarvam_api_key="x",
            openai_api_key="x",
            redis_url="redis://localhost:6379",
            twilio_from_number="+16295550100",
        )
    )
    tts._connect = lambda: asyncio.sleep(0)
    tts.create_task = lambda coro: coro.close()

    async def _run():
        from pipecat.frames.frames import StartFrame

        await tts.start(StartFrame())

    asyncio.run(_run())
    assert tts._speech_sample_rate == str(tts.sample_rate)
    assert isinstance(tts._speech_sample_rate, str), "the websocket API wants a string"


def test_a_failed_tts_prewarm_does_not_take_down_pipeline_start():
    """run_tts reconnects on demand, so a failed prewarm must be survivable."""
    import asyncio

    from roma.config import Settings
    from roma.telephony.media import build_tts

    tts = build_tts(
        Settings(
            sarvam_api_key="x",
            openai_api_key="x",
            redis_url="redis://localhost:6379",
            twilio_from_number="+16295550100",
        )
    )

    async def _boom():
        raise RuntimeError("handshake failed")

    tts._connect = _boom
    asyncio.run(tts._connect_in_background())  # must not raise


def test_a_concurrent_connect_does_not_open_a_second_socket():
    """Call 36598d8f — a totally silent call, caused by the prewarm added for 16835e83.

        20:05:32.346  Connected to Sarvam TTS Websocket   (prewarm)
        20:05:32.346  Generating TTS [Hello Nihit ji...]
        20:05:32.347  Connected to Sarvam TTS Websocket   (run_tts, AGAIN)

    `run_tts` opens with "if the socket is missing or closed, connect". It checked in the
    same tick the prewarm was assigning, so it opened a SECOND socket and replaced the
    first — the greeting went out on one while the receive task listened on the other.
    """
    import asyncio

    from roma.config import Settings
    from roma.telephony.media import build_tts

    tts = build_tts(
        Settings(
            sarvam_api_key="x",
            openai_api_key="x",
            redis_url="redis://localhost:6379",
            twilio_from_number="+16295550100",
        )
    )

    opened = {"n": 0}

    class _Sock:
        state = object()  # never State.CLOSED

    async def _slow_open():
        await asyncio.sleep(0.05)  # the window the real handshake leaves open
        opened["n"] += 1
        tts._websocket = _Sock()

    # Patch the PARENT's connect: our override is the thing under test.
    import pipecat.services.sarvam.tts as sarvam_tts

    original = sarvam_tts.SarvamTTSService._connect
    sarvam_tts.SarvamTTSService._connect = lambda self: _slow_open()
    try:

        async def _run():
            await asyncio.gather(tts._connect(), tts._connect(), tts._connect())

        asyncio.run(_run())
    finally:
        sarvam_tts.SarvamTTSService._connect = original

    assert opened["n"] == 1, f"opened {opened['n']} sockets; the loser must be a no-op"


def test_a_closed_socket_is_still_reconnected():
    """Idempotence must not become 'never reconnect' — run_tts relies on this path when the
    socket drops mid-call."""
    import asyncio

    from websockets.protocol import State

    from roma.config import Settings
    from roma.telephony.media import build_tts

    tts = build_tts(
        Settings(
            sarvam_api_key="x",
            openai_api_key="x",
            redis_url="redis://localhost:6379",
            twilio_from_number="+16295550100",
        )
    )

    class _Dead:
        state = State.CLOSED

    tts._websocket = _Dead()
    opened = {"n": 0}

    async def _open():
        opened["n"] += 1

    import pipecat.services.sarvam.tts as sarvam_tts

    original = sarvam_tts.SarvamTTSService._connect
    sarvam_tts.SarvamTTSService._connect = lambda self: _open()
    try:
        asyncio.run(tts._connect())
    finally:
        sarvam_tts.SarvamTTSService._connect = original

    assert opened["n"] == 1, "a closed socket must be replaced, not kept"


def test_an_exhausted_stt_connect_does_not_tear_the_call_down():
    """Call 335aa291. Sarvam's STT never connected; the retry gave up after 3 tries in ~2s
    and RE-RAISED, which pushed an ErrorFrame into the pipeline. Two symptoms followed, and
    the lead reported them as separate bugs:

      12:40:39  ERROR NonBlockingStartSarvamSTT#2 exception
      12:40:48  greeting spoken a SECOND time   (no transcripts -> silence watchdog nudge)
      12:40:56  call hung up                    (stt_alive=False -> CallCloser)

    A call with no STT is degraded, not doomed. CallHealth decides what to do about a deaf
    leg; killing the pipeline takes that decision away from it."""
    import asyncio

    from roma.telephony.media import NonBlockingStartSarvamSTT

    stt = NonBlockingStartSarvamSTT.__new__(NonBlockingStartSarvamSTT)
    calls = {"n": 0}

    async def _always_fails():
        calls["n"] += 1
        raise TimeoutError("timed out during opening handshake")

    stt._connect = _always_fails
    import roma.telephony.media as media_mod

    orig, orig_max = media_mod.STT_CONNECT_RETRY_SECS, media_mod.STT_CONNECT_RETRY_MAX_SECS
    media_mod.STT_CONNECT_RETRY_SECS = 0.001
    media_mod.STT_CONNECT_RETRY_MAX_SECS = 0.002
    try:
        asyncio.run(stt._connect_with_retry())  # must NOT raise
    finally:
        media_mod.STT_CONNECT_RETRY_SECS = orig
        media_mod.STT_CONNECT_RETRY_MAX_SECS = orig_max

    assert calls["n"] == media_mod.STT_CONNECT_ATTEMPTS


def test_the_retry_window_is_wide_enough_to_outlast_a_bad_handshake():
    """3 tries 1s apart covered a 2s window against a link measured at p50 3.0s with 40%
    outright timeouts — a formality, not a retry."""
    from roma.telephony.media import (
        STT_CONNECT_ATTEMPTS,
        STT_CONNECT_RETRY_MAX_SECS,
        STT_CONNECT_RETRY_SECS,
    )

    delay, total = STT_CONNECT_RETRY_SECS, 0.0
    for _ in range(STT_CONNECT_ATTEMPTS - 1):
        total += delay
        delay = min(delay * 2, STT_CONNECT_RETRY_MAX_SECS)
    assert total >= 25.0, f"only {total:.0f}s of retry window"
