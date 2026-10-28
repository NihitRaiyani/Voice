import asyncio
import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from roma.domain.conversation.state import CallState
from roma.eval.tts import SyntheticPCMProvider
from roma.providers.ai.mocks import MockLLMProvider
from roma.services.safety_service import SafetyService
from roma.services.speech_input import SpeechTranscript
from roma.services.text_conversation_service import TextConversationService
from roma.services.tts_service import SpeechSynthesisService
from roma.services.voice_prototype import (
    InMemoryVoiceTurnStore,
    TimedLLMProvider,
    VoicePrototype,
)


class Speaker:
    def __init__(self):
        self.played = []

    async def play(self, result):
        self.played.append(result)


def setup(store=None, speaker=None, response="नमस्ते।"):
    state = CallState(call_sid="voice-lab-test")
    safety = SafetyService()
    text = TextConversationService(
        TimedLLMProvider(MockLLMProvider(response=response)),
        safety=safety,
        dialogue_language="voice_hi",
    )
    synth = SpeechSynthesisService(SyntheticPCMProvider(), safety, model_version="synthetic-v1")
    return VoicePrototype(
        state, text, synth, speaker or Speaker(), store or InMemoryVoiceTurnStore()
    )


def transcript(text="fees 50000", **changes):
    return replace(SpeechTranscript(text, "hi", 500, "silence", 2.0), **changes)


@pytest.mark.parametrize(
    "text", ["ફી 50000", "फीस 50000", "fees 50000", "fees kitni hai 50000"]
)
def test_language_paths_preserve_safe_output_and_save_before_speaker(text):
    async def run():
        voice = setup()

        async def play(result):
            assert voice.store.records[-1][1]["status"] == "ready"
            voice.speaker.played.append(result)

        voice.speaker.play = play
        outcome = await voice.respond(
            transcript(text), {"capture_ms": 500.0, "vad_endpoint_ms": 3.0}
        )
        assert outcome.status == "played" and outcome.error_code is None
        assert len(voice.speaker.played) == 1
        assert "50000" not in voice.store.records[-1][1]["agent"]
        assert {
            "asr_ms",
            "llm_ms",
            "routing_controller_ms",
            "safety_normalization_ms",
            "tts_cache_frames_ms",
            "playback_ms",
        } <= outcome.timings.keys()
        assert voice.store.records[-1][1]["turn_number"] == voice.state.turn_count
        assert voice.store.records[-1][1]["status"] == "played"

    asyncio.run(run())


@pytest.mark.parametrize("error", [None, "stt-unavailable"])
def test_empty_or_failed_asr_never_advances_or_speaks(error):
    async def run():
        voice = setup()
        result = await voice.respond(transcript("", error=error))
        assert result.status == "failed" and voice.state.turn_count == 0
        assert not voice.speaker.played
        assert voice.store.records[-1][1]["error_code"] in {"ASR"}

    asyncio.run(run())


def test_persistence_failure_prevents_provider_and_playback():
    class Store:
        async def save(self, *args):
            raise OSError("private DB details")

    async def run():
        voice = setup(store=Store())
        with pytest.raises(RuntimeError, match=r"^DB:"):
            await voice.respond(transcript())
        assert not voice.speaker.played and voice.state.turn_count == 0
        with pytest.raises(RuntimeError, match="restart"):
            await voice.respond(transcript())

    asyncio.run(run())


def test_speaker_failure_records_failure_and_stops_reuse():
    class BrokenSpeaker(Speaker):
        async def play(self, result):
            raise OSError("native failure")

    async def run():
        voice = setup(speaker=BrokenSpeaker())
        with pytest.raises(RuntimeError, match="session stopped"):
            await voice.respond(transcript())
        assert voice.store.records[-1][1]["status"] == "failed"
        assert voice._poisoned

    asyncio.run(run())


def test_concurrent_or_cancelled_turn_cannot_release_late_audio():
    async def run():
        voice = setup()
        started = asyncio.Event()

        async def turn(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        voice.conversation.turn = turn
        task = asyncio.create_task(voice.respond(transcript()))
        await started.wait()
        with pytest.raises(RuntimeError, match="busy"):
            await voice.respond(transcript())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not voice.speaker.played and not voice._busy and voice._poisoned
        assert voice.store.records[-1][1]["error_code"] == "CANCELLED"

    asyncio.run(run())


def test_five_minute_ceiling_rejects_before_any_work():
    async def run():
        voice = setup()
        voice.clock = lambda: voice._started + 300
        with pytest.raises(RuntimeError, match="five-minute"):
            await voice.respond(transcript())
        assert not voice.store.records

    asyncio.run(run())


def test_cli_mock_has_four_paths_and_never_claims_hardware_gate():
    spec = importlib.util.spec_from_file_location(
        "voice_cli", Path(__file__).resolve().parents[2] / "scripts/voice_prototype.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = asyncio.run(module.mock_lab())
    assert len(result["cases"]) == 4 and all(c["status"] == "played" for c in result["cases"])
    assert all(c["restored_state"] for c in result["cases"])
    assert result["local_router_version"] == "whisper-turbo-structural-v3"
    assert [c["decoder_calls"] for c in result["cases"]] == [1] * 4
    assert not result["gate_passed"] and not result["microphone_used"]
    args = module.parser().parse_args(["--mode", "local"])
    with pytest.raises(RuntimeError, match=r"^CAPTURE:"):
        module.local_preflight(args)


def test_invalid_metrics_rejected_before_store():
    async def run():
        for timings in ({"capture_ms": float("nan")}, {"private_transcript": 1}, {"asr_ms": 0}):
            voice = setup()
            with pytest.raises(ValueError):
                await voice.respond(transcript(), timings)
            assert not voice.store.records and not voice._busy

    asyncio.run(run())


def test_discovery_questions_use_exact_local_pronunciation_and_keep_canonical_policy():
    async def run():
        voice = setup()
        result = await voice.respond(transcript("haan"))
        assert result.status == "played"
        assert voice.store.records[-1][1]["agent"] == "आपका नाम क्या है?"

    asyncio.run(run())


def test_local_only_preflight_rejects_hosted_asr_before_devices_or_database(monkeypatch):
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location(
        "voice_preflight_test",
        Path(__file__).resolve().parents[2] / "scripts/voice_prototype.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "SpeechSettings", lambda: SimpleNamespace(stt_provider="hf_public_lab")
    )
    args = module.parser().parse_args(
        ["--mode", "local", "--allow-microphone", "--allow-paid-api", "--allow-database-write"]
    )
    with pytest.raises(RuntimeError, match=r"^ASR:"):
        module.local_preflight(args)


def test_explicit_local_routing_never_switches_after_provider_failure():
    from roma.providers.ai.contracts import (
        AudioFormat,
        ProviderUnavailable,
        STTRequest,
        STTResult,
    )
    from roma.providers.ai.local_routing import LocalLanguageSTT

    class STT:
        def __init__(self, fail=False):
            self.calls = 0
            self.fail = fail

        async def transcribe(self, request):
            self.calls += 1
            if self.fail:
                raise ProviderUnavailable("local model unavailable")
            return STTResult("safe transcript")

    async def run():
        decoder = STT(True)
        provider = LocalLanguageSTT(decoder)
        with pytest.raises(ProviderUnavailable):
            await provider.transcribe(
                STTRequest(b"\0\0", AudioFormat(16000), language_hint="gu")
            )
        assert decoder.calls == 1

    asyncio.run(run())


def test_booking_pronunciation_preserves_slot_and_requires_exact_controller_line():
    from uuid import uuid4

    from roma.domain.appointments.booking import confirmed_line
    from roma.domain.conversation.state import spoken_slot
    from roma.services.voice_pronunciation import render_voice_line

    state = CallState(
        booking_required=True,
        appointment_id=str(uuid4()),
        locked_slot="2026-10-21T18:00:00+05:30",
        branch="Vadodara",
    )
    canonical = confirmed_line(spoken_slot(state.locked_slot), state.branch)
    spoken = render_voice_line(canonical, state)
    assert "शाम छह बजे" in spoken and "वडोदरा" in spoken
    assert render_voice_line(canonical + " extra", state) == canonical + " extra"
    state.appointment_id = None
    assert render_voice_line(canonical, state) == canonical


def test_model_readiness_uses_only_local_synthetic_audio_before_capture():
    spec = importlib.util.spec_from_file_location(
        "voice_readiness_test",
        Path(__file__).resolve().parents[2] / "scripts/voice_prototype.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class STT:
        async def transcribe(self, request):
            assert request.audio == b"\0\0" * 16000
            calls.append(request.language_hint)

    class TTS:
        async def synthesize(self, request):
            calls.append("tts")

    class Scorer:
        def probability(self, pcm):
            calls.append("vad")

        def reset(self):
            calls.append("reset")

    asyncio.run(module.ready_local_models(STT(), TTS(), Scorer()))
    assert calls == ["gu", "hi", "en", "tts", "vad", "reset"]


def test_llm_error_chunk_closes_inner_stream_before_next_turn():
    from roma.providers.ai.contracts import LLMChunk

    class Provider:
        def __init__(self):
            self.closed = 0

        async def generate(self, request):
            try:
                yield LLMChunk("", finish_reason="unavailable")
            finally:
                self.closed += 1

    async def run():
        voice = setup()
        provider = Provider()
        voice.conversation.provider = TimedLLMProvider(provider)
        from roma.domain.conversation.stage import ConversationStage

        voice.state.stage = ConversationStage.VALUE
        await voice.respond(transcript("haan"))
        assert provider.closed >= 1
        assert voice.store.records[-1][1]["error_code"] == "LLM"

    asyncio.run(run())


@pytest.mark.parametrize("language,hint", [("gu", "gu"), ("hi", "hi-en"), ("en", "en")])
def test_explicit_starting_language_reaches_asr_before_transcript_router(language, hint):
    from roma.services.speech_input import language_hint

    spec = importlib.util.spec_from_file_location(
        "voice_language_bootstrap",
        Path(__file__).resolve().parents[2] / "scripts/voice_prototype.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = module.initial_lab_state(language, "Vadodara")
    assert language_hint(state) == hint and state.booking_required
    assert state.call_sid.startswith("voice-lab-")


def test_local_mock_preflight_does_not_read_claude_settings_or_require_paid_opt_in(monkeypatch):
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location(
        "voice_local_mock", Path(__file__).resolve().parents[2] / "scripts/voice_prototype.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "SpeechSettings", lambda: SimpleNamespace(stt_provider="whisper")
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        module, "ClaudeLLMSettings", lambda **kw: pytest.fail("Claude configured")
    )
    args = module.parser().parse_args(
        [
            "--mode",
            "local",
            "--llm-provider",
            "mock",
            "--allow-microphone",
            "--allow-database-write",
        ]
    )
    cfg, llm, tts = module.local_preflight(args)
    assert cfg.stt_provider == "whisper" and llm is None and tts.tts_provider == "indic_tts"
    args.llm_provider = "claude"
    with pytest.raises(RuntimeError, match=r"^LLM:"):
        module.local_preflight(args)


@pytest.mark.parametrize("offer_stage", [False, True])
def test_existing_approved_reoffer_has_exact_local_pronunciation(offer_stage):
    from roma.domain.conversation.confirmguard import (
        SAFE_REOFFER_LINE,
        SAFE_REOFFER_LINE_IN_OFFER,
    )
    from roma.services.voice_pronunciation import render_voice_line

    canonical = SAFE_REOFFER_LINE_IN_OFFER if offer_stage else SAFE_REOFFER_LINE
    rendered = render_voice_line(canonical, CallState())
    assert not any(c.isascii() and c.isalpha() for c in rendered)
    assert rendered == (
        "अभी वो टाइम फाइनल नहीं हुआ है।"
        if offer_stage
        else "अभी वो टाइम फाइनल नहीं हुआ है। आपको सुबह का टाइम ठीक रहेगा या शाम का?"
    )


def test_booking_unavailable_filter_output_uses_the_approved_reoffer_rendering():
    from roma.domain.appointments.booking import BOOKING_UNAVAILABLE_LINE
    from roma.domain.conversation.confirmguard import SAFE_REOFFER_LINE
    from roma.services.text_conversation_service import filter_response
    from roma.services.voice_pronunciation import render_voice_line

    state = CallState(slot_status="unavailable", booking_required=True)
    final, _ = filter_response(BOOKING_UNAVAILABLE_LINE, state)
    assert (
        final == SAFE_REOFFER_LINE
    )  # Preserve existing deterministic business/safety behavior.
    assert render_voice_line(final, state).startswith("अभी वो टाइम फाइनल")
    assert render_voice_line(final + " extra", state) == final + " extra"


def test_verified_conflict_reoffer_has_finite_local_pronunciation():
    from roma.domain.conversation.shortcircuit import canned_reply
    from roma.services.text_conversation_service import filter_response
    from roma.services.voice_pronunciation import render_voice_line

    state = CallState(
        stage="pivot",
        booking_required=True,
        booking_error="APPOINTMENT_SLOT_UNAVAILABLE",
        slot_status="unavailable",
        slots_offered=["2026-10-18T17:00:00+05:30"],
    )
    canonical = canned_reply(state, "tomorrow at 11 AM")
    final, _ = filter_response(canonical, state)
    assert final == canonical and "5:00 PM" in final
    rendered = render_voice_line(final, state)
    assert "शाम पाँच बजे" in rendered
    assert not any(c.isascii() and c.isalpha() for c in rendered)


@pytest.mark.parametrize("route,raw,expected", [
    ("GUJARATI", "વિઝિટ ટાઈમ કાલે", "visit time કાલે"),
    ("HINDI", "विजिट टाइम कल", "visit time कल"),
    ("GUJARATI_ENGLISH", "અપોઈન્ટમેન્ટ કન્ફર્મ કરી દેજો", "appointment confirm કરી દેજો"),
    ("HINDI_ENGLISH", "अपॉइंटमेंट कन्फर्म कर दो", "appointment confirm कर दो"),
    ("ENGLISH", "Please confirm my appointment", "Please confirm my appointment"),
])
def test_single_pass_speech_reaches_controller_safety_and_speaker(
    route, raw, expected
):
    from roma.domain.conversation.language import Language
    from roma.eval.speech import ScriptedProbability, ScriptedSTT
    from roma.providers.ai.local_routing import LocalLanguageSTT
    from roma.services.speech_input import SpeechInputService

    async def run():
        voice = setup()
        voice.state.language_router.current = getattr(Language, route)
        decoder = ScriptedSTT((raw,))
        speech = SpeechInputService(
            LocalLanguageSTT(decoder), ScriptedProbability(), voice.state
        )
        final = []
        for speaking in [1] * 4 + [0] * 20:
            final.extend(await speech.feed(bytes([speaking, 0]) * 512))
        assert len(final) == 1 and final[0].text == expected
        assert decoder.calls == 1
        outcome = await voice.respond(final[0])
        assert outcome.status == "played" and outcome.error_code is None
        assert voice.store.records[-1][1]["caller"] == expected
        assert len(voice.speaker.played) == 1
        assert CallState.from_dict(voice.state.to_dict()).to_dict() == voice.state.to_dict()
        assert {"asr_ms", "llm_ms", "routing_controller_ms", "safety_normalization_ms", "playback_ms"} <= outcome.timings.keys()

    asyncio.run(run())


def test_operational_events_correlate_two_turns_and_do_not_log_transcripts(caplog, monkeypatch):
    import json
    import logging

    from roma.core.logging import JsonFormatter
    monkeypatch.setattr(logging.getLogger("roma.events"), "disabled", False)
    caplog.set_level(logging.INFO, logger="roma.events")
    async def run():
        voice = setup()
        await voice.respond(transcript("fees 50000"))
        await voice.respond(transcript("fees 50000"))
    asyncio.run(run())
    rows = [json.loads(JsonFormatter().format(r)) for r in caplog.records
            if getattr(r, "event", None) is not None]
    completed = [r for r in rows if r["event"] == "turn_completed"]
    assert [r["turn_id"] for r in completed] == [1, 2]
    assert len({r["correlation_id"] for r in completed}) == 1
    assert completed[0]["correlation_id"] is not None
    assert len({r["request_id"] for r in completed}) == 2
    assert all(r["call_id"] == "voice-lab-test" for r in rows)
    for event in ("asr_completed", "controller_completed", "safety_completed", "tts_completed",
                  "playback_completed", "turn_started", "turn_completed"):
        assert sum(r["event"] == event for r in rows) == 2
    assert all("fees 50000" not in v for row in rows for v in row.values() if isinstance(v, str))
    assert all(r["language"] in {"hi", "gu", "en", "hi-en", "gu-en"} for r in rows)


def test_failed_voice_turn_logs_fixed_error_with_context(caplog, monkeypatch):
    import json
    import logging

    from roma.core.logging import JsonFormatter
    monkeypatch.setattr(logging.getLogger("roma.events"), "disabled", False)
    caplog.set_level(logging.INFO, logger="roma.events")
    class Broken(Speaker):
        async def play(self, result):
            raise OSError("private +919876543210")
    async def run():
        with pytest.raises(RuntimeError):
            await setup(speaker=Broken()).respond(transcript())
    asyncio.run(run())
    rows = [json.loads(JsonFormatter().format(r)) for r in caplog.records]
    failed = [r for r in rows if r["event"] == "turn_failed"]
    assert len(failed) == 1 and failed[0]["error_code"] == "DEVICE"
    assert failed[0]["turn_id"] == 1 and failed[0]["level"] == "ERROR"
    assert "private" not in json.dumps(rows)


def test_llm_event_uses_completed_controller_stage_and_language(caplog, monkeypatch):
    import json
    import logging

    from roma.core.logging import JsonFormatter
    from roma.domain.conversation.stage import ConversationStage
    monkeypatch.setattr(logging.getLogger("roma.events"), "disabled", False)
    caplog.set_level(logging.INFO, logger="roma.events")
    async def run():
        voice = setup()
        voice.state.stage = ConversationStage.VALUE
        result = await voice.respond(transcript("haan"))
        return result
    result = asyncio.run(run())
    rows = [json.loads(JsonFormatter().format(r)) for r in caplog.records]
    llm = [r for r in rows if r["event"] == "llm_completed"]
    assert len(llm) == 1
    assert llm[0]["stage"] == result.stage
    assert llm[0]["language"] is not None


def test_mock_cli_logging_needs_no_provider_credentials_or_env_file(tmp_path):
    import json
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/voice_prototype.py"), "--mode", "mock"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "NLTK_DATA": str(root / "var/roma/nltk_data"),
             "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert len(report["cases"]) == 4 and not report["network_calls"]
    events = [json.loads(line) for line in result.stderr.splitlines()]
    assert sum(row["event"] == "turn_completed" for row in events) == 4


def test_remaining_call_budget_reaches_provider_work():
    from roma.providers.reliability import remaining_timeout

    async def run():
        voice = setup()
        voice.clock = lambda: voice._started + 299
        original = voice.conversation.turn
        async def turn(*args, **kwargs):
            assert 0 < remaining_timeout(1000) <= 1
            return await original(*args, **kwargs)
        voice.conversation.turn = turn
        assert (await voice.respond(transcript())).status == "played"
    asyncio.run(run())


@pytest.mark.parametrize('hour,expected', [(5, 'सुबह पाँच बजे'), (17, 'शाम पाँच बजे'),
                                         (0, 'रात बारह बजे'), (12, 'दोपहर बारह बजे')])
def test_controller_readback_keeps_meridian_until_clock_normalization(hour, expected):
    from roma.domain.audio.speech_normalization import normalize_tts_text
    from roma.domain.conversation.confirmguard import SAFE_READBACK_HOLD_LINE
    from roma.domain.conversation.state import spoken_slot
    from roma.services.voice_pronunciation import render_voice_line

    state = CallState(accepted_slot=f'2026-10-29T{hour:02d}:00:00+05:30')
    canonical = SAFE_READBACK_HOLD_LINE.format(slot=spoken_slot(state.accepted_slot))
    rendered = render_voice_line(canonical, state)
    normalized = normalize_tts_text(rendered, 'hi')
    assert expected in normalized
    assert 'पी एम' not in normalized and 'ए एम' not in normalized
    assert 'थर्सडे उनतीस ऑक्टोबर' in normalized
    assert normalized.endswith('— ये सही रहेगा आपके लिए?')
    assert normalize_tts_text(normalized, 'hi') == normalized
