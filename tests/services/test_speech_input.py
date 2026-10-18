import asyncio

import pytest
from roma.domain.audio.endpointing import FRAME_BYTES
from roma.domain.audio.pcm import AudioFormatError
from roma.domain.conversation.language import Language
from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import ProviderUnavailable, STTResult
from roma.services.speech_input import SpeechInputService, language_hint


class Scorer:
    def __init__(self):
        self.resets = 0

    def probability(self, pcm):
        assert len(pcm) == FRAME_BYTES
        return 0.9 if pcm[0] else 0.0

    def reset(self):
        self.resets += 1


class STT:
    def __init__(self):
        self.requests = []

    async def transcribe(self, request):
        self.requests.append(request)
        return STTResult("haan", "hi")


def test_transport_20ms_chunks_reframe_and_submit_only_at_endpoint():
    async def run():
        stt, scorer, state = STT(), Scorer(), CallState()
        service = SpeechInputService(stt, scorer, state)
        results = []
        for _ in range(8):
            results.extend(await service.feed(b"\1\0" * 320))
        assert not stt.requests
        for _ in range(28):
            results.extend(await service.feed(b"\0\0" * 320))
        assert len(stt.requests) == len(results) == 1
        assert results[0].text == "haan" and results[0].reason == "silence"
        assert stt.requests[0].format.sample_rate_hz == 16000
        assert state.language_router.recent == []  # Accepted-turn controller owns this update.
        assert 0 <= service.pending_bytes < FRAME_BYTES

    asyncio.run(run())


def test_gap_preserves_old_speech_resets_scorer_and_does_not_bridge_audio():
    async def run():
        stt, scorer = STT(), Scorer()
        service = SpeechInputService(stt, scorer, CallState())
        await service.feed(b"\1\0" * 1200)
        results = await service.feed(b"\0\0" * 320, discontinuity=True)
        assert len(results) == 1 and results[0].reason == "discontinuity"
        assert scorer.resets == 1 and service.pending_bytes == 640
        assert len(stt.requests[0].audio) == 2048

    asyncio.run(run())


def test_final_tail_requires_explicit_policy_and_reset_discards():
    async def run():
        service = SpeechInputService(STT(), Scorer(), CallState())
        await service.feed(b"\1\0" * 320)
        with pytest.raises(AudioFormatError):
            await service.finish()
        results = await service.finish(pad_tail=True)
        assert len(results) == 1 and results[0].reason == "flush"
        assert await service.finish() == ()
        await service.feed(b"\1\0" * 320)
        service.reset()
        assert service.pending_bytes == 0
        assert await service.finish() == ()

    asyncio.run(run())


@pytest.mark.parametrize(
    "pcm,kwargs",
    [(b"x", {}), (b"\0" * 32002, {}), (b"", {"sample_rate": 8000}), (b"", {"channels": 2})],
)
def test_bad_input_never_mutates_state(pcm, kwargs):
    async def run():
        service = SpeechInputService(STT(), Scorer(), CallState())
        with pytest.raises(AudioFormatError):
            await service.feed(pcm, **kwargs)
        assert service.pending_bytes == service.endpoint.buffered_bytes == 0

    asyncio.run(run())


def test_provider_error_is_sanitized_and_does_not_emit_a_transcript():
    class Broken:
        async def transcribe(self, request):
            raise ProviderUnavailable("secret-and-transcript")

    async def run():
        service = SpeechInputService(Broken(), Scorer(), CallState())
        await service.feed(b"\1\0" * 512)
        (result,) = await service.finish()
        assert result.error == "stt-unavailable" and result.text == ""
        assert "secret" not in repr(result)

    asyncio.run(run())


def test_overlap_rejected_and_cancellation_discards_endpoint_state():
    class Waiting:
        async def transcribe(self, request):
            await asyncio.sleep(60)

    async def run():
        service = SpeechInputService(Waiting(), Scorer(), CallState())
        await service.feed(b"\1\0" * 1024)
        task = asyncio.create_task(service.finish())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError):
            await service.feed(b"")
        with pytest.raises(RuntimeError):
            service.reset()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.pending_bytes == service.endpoint.buffered_bytes == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "route,hint",
    [
        (Language.GUJARATI, "gu"),
        (Language.GUJARATI_ENGLISH, "gu"),
        (Language.HINDI, "hi"),
        (Language.HINDI_ENGLISH, "hi"),
        (Language.ENGLISH, "en"),
    ],
)
def test_language_hint_uses_saved_route_without_changing_output_policy(route, hint):
    state = CallState()
    state.language_router.current = route
    assert language_hint(state) == hint


def test_final_transcript_flows_through_controller_and_saves_language_once():
    from datetime import datetime

    from roma.domain.appointments.timeresolve import IST
    from roma.domain.conversation.state_machine import (
        InMemoryConversationStateStore,
        restore_state,
    )
    from roma.providers.ai.mocks import MockLLMProvider
    from roma.services.text_conversation_service import TextConversationService

    class GujaratiSTT:
        async def transcribe(self, request):
            return STTResult("મારું નામ અમિત છે", "gu")

    async def run():
        state = CallState(call_sid="synthetic-speech-controller")
        speech = SpeechInputService(GujaratiSTT(), Scorer(), state)
        store = InMemoryConversationStateStore()
        text = TextConversationService(MockLLMProvider(), store=store)
        await speech.feed(b"\1\0" * 1024)
        (final,) = await speech.finish()
        assert not state.language_router.recent
        await text.turn(state, final.text, now=datetime.now(IST))
        assert state.language_router.current == Language.GUJARATI
        assert len(state.language_router.recent) == 1
        restored = await restore_state(store, state.call_sid)
        assert restored.language_router == state.language_router
        assert restored.turn_count == state.turn_count

    asyncio.run(run())


def test_english_route_does_not_abort_or_relabel_indic_request():
    from roma.providers.ai.stt import IndicConformerSTT

    async def run():
        state = CallState()
        state.language_router.current = Language.ENGLISH
        service = SpeechInputService(IndicConformerSTT(), Scorer(), state)
        await service.feed(b"\1\0" * 1024)
        (result,) = await service.finish()
        assert result.text == ""
        assert result.error == "stt-unsupported-or-invalid-request"
        assert state.language_router.current == Language.ENGLISH

    asyncio.run(run())
