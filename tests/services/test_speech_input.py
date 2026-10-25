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
        (Language.GUJARATI_ENGLISH, "gu-en"),
        (Language.HINDI, "hi"),
        (Language.HINDI_ENGLISH, "hi-en"),
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


def test_quiet_continuation_crosses_asr_size_chunks_without_audio_loss():
    from roma.domain.audio.endpointing import EndpointConfig, EndpointDetector

    class HysteresisScorer(Scorer):
        def probability(self, pcm):
            return 0.9 if pcm[0] == 1 else 0.4

    async def run():
        stt = STT()
        service = SpeechInputService(
            stt,
            HysteresisScorer(),
            CallState(),
            endpoint=EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193)),
        )
        frames = [b"\1\0" * 512] * 2 + [b"\2\0" * 512] * 8
        results = []
        for frame in frames:
            results.extend(await service.feed(frame))
        results.extend(await service.finish())
        assert [result.reason for result in results] == ["flush"]
        assert results[0].text == "haan haan"
        assert b"".join(request.audio for request in stt.requests) == b"".join(frames)
        assert all(len(request.audio) <= 6 * FRAME_BYTES for request in stt.requests)
        assert service.pending_bytes == 0

    asyncio.run(run())


def test_metadata_is_copied_and_forwarded_only_to_asr():
    async def run():
        stt = STT()
        metadata = {"data_classification": "public_benchmark"}
        service = SpeechInputService(stt, Scorer(), CallState(), stt_metadata=metadata)
        metadata["data_classification"] = "changed"
        await service.feed(b"\1\0" * 1024)
        await service.finish()
        assert stt.requests[0].metadata == {"data_classification": "public_benchmark"}

    asyncio.run(run())


def test_max_chunks_are_accepted_only_once_after_actual_silence():
    from roma.domain.audio.endpointing import EndpointConfig, EndpointDetector

    async def run():
        stt = STT()
        service = SpeechInputService(
            stt,
            Scorer(),
            CallState(),
            endpoint=EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193)),
        )
        for _ in range(18):
            assert await service.feed(b"\1\0" * 512) == ()
        assert len(stt.requests) == 3
        for _ in range(15):
            assert await service.feed(b"\0\0" * 512) == ()
        (final,) = await service.feed(b"\0\0" * 512)
        assert final.text == "haan haan haan" and final.reason == "silence"
        assert final.duration_ms == 576 and final.error is None
        assert len(stt.requests) == 3  # Empty final boundary makes no ASR request.
        assert await service.finish() == ()

    asyncio.run(run())


def small_endpoint():
    from roma.domain.audio.endpointing import EndpointConfig, EndpointDetector

    return EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193))


@pytest.mark.parametrize("boundary", ["flush", "discontinuity"])
def test_exact_max_boundary_finalizes_once_and_next_turn_starts_fresh(boundary):
    async def run():
        stt = STT()
        service = SpeechInputService(stt, Scorer(), CallState(), endpoint=small_endpoint())
        assert await service.feed(b"\1\0" * (512 * 6)) == ()
        if boundary == "flush":
            results = await service.finish()
        else:
            results = await service.feed(b"", discontinuity=True)
        assert len(results) == 1 and results[0].reason == boundary
        assert results[0].text == "haan" and len(stt.requests) == 1
        assert await service.finish() == ()
        await service.feed(b"\1\0" * 1024)
        (fresh,) = await service.finish()
        assert fresh.text == "haan" and fresh.duration_ms == 64

    asyncio.run(run())


def test_continuous_turn_text_is_bounded_and_overflow_is_sticky_until_final():
    from roma.domain.safety.inputs import CALLER_TEXT_LIMIT

    class VerboseSTT(STT):
        async def transcribe(self, request):
            self.requests.append(request)
            return STTResult("x" * (CALLER_TEXT_LIMIT // 2), "hi")

    async def run():
        stt = VerboseSTT()
        service = SpeechInputService(stt, Scorer(), CallState(), endpoint=small_endpoint())
        for _ in range(100):
            assert await service.feed(b"\1\0" * (512 * 6)) == ()
            assert service._turn_chars <= CALLER_TEXT_LIMIT
            assert sum(map(len, service._turn_parts)) <= CALLER_TEXT_LIMIT
        (final,) = await service.finish()
        assert final.text == "" and final.language is None
        assert final.error == "stt-turn-too-long" and final.reason == "flush"
        assert await service.finish() == ()
        await service.feed(b"\1\0" * 1024)
        (fresh,) = await service.finish()
        assert fresh.error is None and len(fresh.text) == CALLER_TEXT_LIMIT // 2

    asyncio.run(run())


@pytest.mark.parametrize("failure", [ProviderUnavailable, ValueError])
def test_any_failed_chunk_rejects_the_whole_turn_and_next_turn_recovers(failure):
    class IntermittentSTT(STT):
        async def transcribe(self, request):
            self.requests.append(request)
            if len(self.requests) == 2:
                raise failure("private provider details")
            return STTResult("haan", "hi")

    async def run():
        stt = IntermittentSTT()
        service = SpeechInputService(stt, Scorer(), CallState(), endpoint=small_endpoint())
        for _ in range(3):
            assert await service.feed(b"\1\0" * (512 * 6)) == ()
        (final,) = await service.finish()
        assert final.error and final.text == "" and final.language is None
        assert "private" not in repr(final)
        assert len(stt.requests) == 3
        await service.feed(b"\1\0" * 1024)
        (fresh,) = await service.finish()
        assert fresh.text == "haan" and fresh.error is None

    asyncio.run(run())


def test_reset_discards_max_chunk_text_without_emitting_a_phantom_final():
    async def run():
        service = SpeechInputService(STT(), Scorer(), CallState(), endpoint=small_endpoint())
        assert await service.feed(b"\1\0" * (512 * 6)) == ()
        service.reset()
        assert await service.finish() == ()
        await service.feed(b"\1\0" * 1024)
        (fresh,) = await service.finish()
        assert fresh.text == "haan" and fresh.duration_ms == 64

    asyncio.run(run())


def test_cancelled_chunk_discards_previous_transcript_and_final_boundary():
    class WaitingSTT(STT):
        async def transcribe(self, request):
            self.requests.append(request)
            if len(self.requests) == 2:
                await asyncio.sleep(60)
            return STTResult("haan", "hi")

    async def run():
        service = SpeechInputService(
            WaitingSTT(), Scorer(), CallState(), endpoint=small_endpoint()
        )
        assert await service.feed(b"\1\0" * (512 * 6)) == ()
        task = asyncio.create_task(service.feed(b"\1\0" * (512 * 6)))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await service.finish() == ()
        await service.feed(b"\1\0" * 1024)
        (fresh,) = await service.finish()
        assert fresh.text == "haan" and fresh.duration_ms == 64

    asyncio.run(run())


def test_one_batch_can_finalize_multiple_caller_turns_without_mixing_text():
    from roma.domain.audio.endpointing import EndpointConfig, EndpointDetector

    async def run():
        stt = STT()
        service = SpeechInputService(
            stt,
            Scorer(),
            CallState(),
            endpoint=EndpointDetector(
                EndpointConfig(
                    pre_roll_ms=0,
                    hangover_ms=32,
                    silence_ms=64,
                    max_utterance_ms=193,
                )
            ),
        )
        turn_audio = b"\1\0" * (512 * 6) + b"\0\0" * (512 * 2)
        results = await service.feed(turn_audio * 2)
        assert [result.text for result in results] == ["haan", "haan"]
        assert [result.reason for result in results] == ["silence", "silence"]
        assert len(stt.requests) == 2
        assert await service.finish() == ()

    asyncio.run(run())


def test_presegmented_decoder_never_mutates_live_endpoint_or_scorer():
    from roma.core.turn_epoch import TurnEpoch
    from roma.domain.audio.endpointing import Utterance

    async def run():
        guard, provider, scorer = TurnEpoch(), STT(), Scorer()
        live = SpeechInputService(provider, scorer, CallState())
        await live.feed(b'\1\0' * 320)
        decoder = live.segment_decoder(epoch_guard=guard)
        segment = Utterance(b'\2\0' * 512, 0, 512, 1, 'silence', 512)
        result = await decoder.transcribe_segment(segment, turn_epoch=guard.advance())
        assert result.text == 'haan' and result.turn_epoch == 1
        assert live.pending_bytes == 640 and scorer.resets == 0
        assert live.endpoint.position_samples == 0
        guard.advance()
        with pytest.raises(asyncio.CancelledError):
            await decoder.transcribe_segment(segment, turn_epoch=1)
        assert len(provider.requests) == 1
        assert live.pending_bytes == 640 and scorer.resets == 0
    asyncio.run(run())
