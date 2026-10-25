import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from roma.domain.appointments.booking import BookingError
from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import AudioFormat, TTSResult
from roma.services.speech_input import SpeechTranscript
from roma.services.tts_service import SynthesizedSpeech
from roma.services.voice_prototype import InMemoryVoiceTurnStore, VoicePrototype

PRIVATE = "caller-secret +919876543210 /private/caller.wav hf_private_token"


def build_voice(seam):
    state = CallState(call_sid="voice-lab-errors")
    store = InMemoryVoiceTurnStore()

    async def turn(*args, **kw):
        if seam == "LLM":
            raise RuntimeError(PRIVATE)
        if seam == "BOOKING-DATA":
            raise BookingError("BOOKING_UNAVAILABLE")
        return SimpleNamespace(final_response="नमस्ते।", provider_error=None)

    async def synthesize(*args):
        if seam == "TTS":
            raise RuntimeError(PRIVATE)
        return SynthesizedSpeech(
            text="नमस्ते।", mulaw_frames=(), cache_status="miss", rtf=0.1,
            model_version="synthetic-errors", canonical_text="नमस्ते।",
            requested_language="hi", spoken_language="hi", voice="synthetic",
            pronunciation_version=None, canonical_sha256=None, spoken_sha256=None,
            result=TTSResult(b"\0\0" * 160, AudioFormat(16000), duration_ms=10),
            synthesis_ms=1, safety_normalization_ms=1, elapsed_ms=2,
            audit_ok=seam != "SAFETY", spoken_canonical_text="नमस्ते।",
        )

    async def play(*args):
        if seam == "DEVICE":
            raise OSError(PRIVATE)

    if seam == "DB":
        original = store.save
        calls = 0

        async def save(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(PRIVATE)
            await original(*args)

        store.save = save
    return VoicePrototype(
        state, SimpleNamespace(provider=SimpleNamespace(elapsed_ms=0), turn=turn),
        SimpleNamespace(synthesize=synthesize), SimpleNamespace(play=play), store,
    )


@pytest.mark.parametrize("seam", ["ASR", "TTS", "DB", "DEVICE", "CAPTURE", "LLM", "SAFETY", "BOOKING-DATA"])
def test_each_fake_failure_has_its_own_safe_durable_code(seam, capsys, caplog):
    async def run():
        voice = build_voice(seam)
        transcript = SpeechTranscript("synthetic", "en", 100, "silence", 1)
        if seam in {"ASR", "CAPTURE"}:
            transcript = replace(transcript, text="", error="stt-unavailable" if seam == "ASR" else "listen-timeout")
        message = ""
        try:
            outcome = await voice.respond(transcript)
            code = outcome.error_code
        except RuntimeError as exc:
            message = str(exc)
            code = voice.store.records[-1][1]["error_code"]
        assert code == seam
        assert voice.store.records[-1][1]["error_code"] == seam
        assert PRIVATE not in message + capsys.readouterr().err + caplog.text
        for private in PRIVATE.split():
            assert private not in message
        if message:
            assert message.startswith(seam + ":")

    asyncio.run(run())


def test_cli_prints_only_a_safe_stage_code(monkeypatch, capsys):
    import scripts.voice_prototype as cli

    async def fail():
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(cli, "mock_lab", fail)
    monkeypatch.setattr("sys.argv", ["voice_prototype", "--mode", "mock"])
    assert cli.main() == 1
    message = capsys.readouterr().err
    assert message.startswith("CAPTURE:")
    assert all(value not in message for value in PRIVATE.split())


def test_checkpoint_failure_is_db_not_llm():
    from tests.services.test_voice_prototype import setup, transcript

    async def run():
        voice = setup()

        async def save(state):
            raise OSError(PRIVATE)

        voice.conversation.store = SimpleNamespace(save=save)
        with pytest.raises(RuntimeError, match=r"^DB:"):
            await voice.respond(transcript())
        assert voice.store.records[-1][1]["error_code"] == "DB"

    asyncio.run(run())


@pytest.mark.parametrize("code", ["ASR", "TTS", "DB", "DEVICE", "CAPTURE", "LLM", "SAFETY", "BOOKING-DATA"])
def test_cli_preserves_each_typed_code_without_private_details(code, monkeypatch, capsys):
    import scripts.voice_prototype as cli
    from roma.core.voice_errors import VoiceErrorCode, VoiceFailure, map_voice_error

    async def fail():
        raise VoiceFailure(map_voice_error(RuntimeError(PRIVATE), stage=VoiceErrorCode(code)))

    monkeypatch.setattr(cli, "mock_lab", fail)
    monkeypatch.setattr("sys.argv", ["voice_prototype", "--mode", "mock"])
    assert cli.main() == 1
    assert capsys.readouterr().err.strip() == f"{code}: voice session stopped."
