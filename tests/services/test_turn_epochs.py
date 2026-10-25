"""Adversarial turn invalidation uses scripted providers and no native devices."""

import asyncio

import pytest
from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import STTResult
from roma.services.speech_input import SpeechInputService
from tests.services.test_speech_input import Scorer


def test_late_asr_from_invalidated_capture_never_returns_a_final():
    from roma.core.turn_epoch import TurnEpoch

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        guard = TurnEpoch()
        seen = []

        class ASR:
            async def transcribe(self, request):
                seen.append(request.turn_epoch)
                started.set()
                await release.wait()
                return STTResult("Synthetic old result", "hi")

        speech = SpeechInputService(ASR(), Scorer(), CallState(), epoch_guard=guard)
        await speech.feed(b"\1\0" * 1024)
        task = asyncio.create_task(speech.finish())
        await started.wait()
        guard.advance()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen == [0] and speech.pending_bytes == 0

    asyncio.run(run())


def test_interrupt_flushes_before_joining_resistant_provider_within_fake_60ms_target():
    from time import perf_counter

    from tests.services.test_streaming_voice import stream_voice
    from tests.services.test_voice_prototype import transcript

    async def run():
        voice = stream_voice()
        entered, release, cleared = asyncio.Event(), asyncio.Event(), asyncio.Event()
        initial = voice.epochs.snapshot()
        clear_epochs = []

        async def turn(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            raise asyncio.CancelledError

        async def clear():
            clear_epochs.append(voice.epochs.snapshot())
            cleared.set()

        voice.conversation.turn, voice.speaker.clear = turn, clear
        response = asyncio.create_task(voice.respond(transcript("haan")))
        await entered.wait()
        started = perf_counter()
        interrupt = asyncio.create_task(voice.interrupt())
        try:
            await asyncio.wait_for(cleared.wait(), 0.06)
            assert (perf_counter() - started) * 1000 < 60
            assert clear_epochs[0] > initial
        finally:
            release.set()
            await asyncio.gather(response, interrupt, return_exceptions=True)

    asyncio.run(run())


def test_cancel_resistant_old_extraction_cannot_mutate_a_discovery_slot():
    from roma.core.ai_contracts import LLMChunk
    from roma.domain.conversation.stage import ConversationStage
    from tests.services.test_streaming_voice import stream_voice
    from tests.services.test_voice_prototype import transcript

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        class OldLLM:
            async def generate(self, request):
                requests.append(request)
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                yield LLMChunk('{"value":"student","confidence":0.99}', finish_reason="stop")

        voice = stream_voice(OldLLM())
        voice.state.stage = ConversationStage.DISCOVER
        voice.state.lead_name = "Synthetic learner"
        async def clear():
            release.set()
        voice.speaker.clear = clear
        response = asyncio.create_task(voice.respond(transcript("I am studying")))
        await asyncio.wait_for(entered.wait(), 1)
        await voice.interrupt()
        await asyncio.gather(response, return_exceptions=True)
        assert voice.state.current_status is None
        assert not voice.speaker.played
        assert requests[0].turn_epoch == 1
        assert "turn_epoch" not in requests[0].metadata

    asyncio.run(run())


def test_half_duplex_interrupt_discards_late_tts_and_ready_receipt():
    from tests.services.test_voice_prototype import setup, transcript

    async def run():
        voice = setup()
        entered, release = asyncio.Event(), asyncio.Event()
        original = voice.synthesis.synthesize
        observed = []

        async def synthesize(request, *args, **kwargs):
            observed.append(request.turn_epoch)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return await original(request, *args, **kwargs)

        async def clear():
            release.set()

        voice.synthesis.synthesize, voice.speaker.clear = synthesize, clear
        task = asyncio.create_task(voice.respond(transcript("haan")))
        await entered.wait()
        try:
            assert await voice.interrupt()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert observed == [1] and not voice.speaker.played
        assert all(row[1]["status"] != "ready" for row in voice.store.records)
        assert voice.epochs.snapshot() > 0

    asyncio.run(run())


def test_stale_booking_admission_cannot_start_a_sql_reservation():
    from datetime import UTC, date, datetime, time
    from uuid import uuid4

    from roma.core.turn_epoch import TurnEpoch, epoch_scope
    from roma.domain.persistence import AppointmentRecord
    from roma.services.booking_service import BookingService

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        guard, reservations = TurnEpoch(), []
        record = AppointmentRecord(
            id=uuid4(), call_id=uuid4(), branch_id=uuid4(),
            appointment_date=date(2026, 10, 26), start_time=time(11), status="confirmed",
            created_at=datetime(2026, 10, 25, tzinfo=UTC),
            updated_at=datetime(2026, 10, 25, tzinfo=UTC),
        )

        class Store:
            async def get(self, identity):
                entered.set()
                await release.wait()
                return None

            async def available(self, key):
                return True

            async def reserve(self, value):
                reservations.append(value)
                return value

        service = BookingService(Store(), now=lambda: datetime(2026, 10, 25, tzinfo=UTC))
        with epoch_scope(guard, guard.snapshot()):
            task = asyncio.create_task(service.confirm(record))
        await entered.wait()
        guard.advance()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reservations == []

    asyncio.run(run())


def test_stale_tts_is_not_cached_and_cache_hits_get_current_epoch_envelopes():
    from roma.core.turn_epoch import TurnEpoch, epoch_scope
    from roma.domain.safety.audit import SafetyContext
    from roma.providers.ai.contracts import AudioFormat, TTSRequest, TTSResult
    from roma.services.safety_service import SafetyService
    from roma.services.tts_service import SpeechSynthesisService

    async def run():
        guard = TurnEpoch()
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        class TTS:
            async def synthesize(self, request):
                requests.append(request)
                entered.set()
                await release.wait()
                return TTSResult(b"\0\0" * 800, AudioFormat(8000), turn_epoch=request.turn_epoch)

        synthesis = SpeechSynthesisService(TTS(), SafetyService(), model_version="epoch-synthetic")
        context = SafetyContext("voice-lab-epoch-cache", 1, "discover")
        with epoch_scope(guard, guard.snapshot()):
            stale = asyncio.create_task(synthesis.synthesize(TTSRequest("नमस्ते।", turn_epoch=0), context))
        await entered.wait()
        guard.advance()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stale
        assert synthesis.stores == 0
        for _ in range(2):
            epoch = guard.snapshot()
            with epoch_scope(guard, epoch):
                result = await synthesis.synthesize(TTSRequest("नमस्ते।", turn_epoch=epoch), context)
            assert result.result.turn_epoch == epoch
            guard.advance()
        assert synthesis.stores == 1 and synthesis.hits == 1 and len(requests) == 2

    asyncio.run(run())


def test_local_language_wrapper_preserves_internal_epochs_without_metadata_leak():
    from roma.providers.ai.contracts import AudioFormat, STTRequest, STTResult
    from roma.providers.ai.local_routing import LocalLanguageSTT

    async def run():
        seen = []

        class ASR:
            async def transcribe(self, request):
                seen.append(request)
                return STTResult("Synthetic", "hi", turn_epoch=7)

        result = await LocalLanguageSTT(ASR()).transcribe(
            STTRequest(b"\0\0" * 512, AudioFormat(16000), "hi-en", turn_epoch=7)
        )
        assert seen[0].turn_epoch == result.turn_epoch == 7
        assert "turn_epoch" not in seen[0].metadata

    asyncio.run(run())


def test_each_untagged_turn_advances_epoch_but_a_captured_epoch_is_preserved():
    from dataclasses import replace

    from tests.services.test_voice_prototype import setup, transcript

    async def run():
        voice = setup()
        first = await voice.respond(transcript("haan"))
        old_epoch = voice.epochs.snapshot()
        assert first.status == "played"
        await voice.respond(transcript("haan"))
        assert voice.epochs.snapshot() > old_epoch
        with pytest.raises(asyncio.CancelledError):
            await voice.respond(replace(transcript("haan"), turn_epoch=old_epoch))
        captured = voice.epochs.advance()
        await voice.respond(replace(transcript("haan"), turn_epoch=captured))
        assert voice.epochs.snapshot() == captured

    asyncio.run(run())


def test_next_turn_restores_sql_commit_after_old_epoch_lost_its_result():
    from datetime import datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from roma.core.turn_epoch import TurnEpoch, epoch_scope
    from roma.domain.appointments.timeresolve import IST
    from roma.domain.conversation.stage import ConversationStage
    from roma.providers.ai.mocks import MockLLMProvider
    from roma.services.booking_session import BookingSession
    from roma.services.text_conversation_service import TextConversationService
    from tests.services.test_booking_service import NOW, record, setup

    async def run():
        service, store, _ = setup()
        item = record(call_id=uuid4(), caller_id=uuid4())
        store.call.return_value = SimpleNamespace(id=item.call_id, caller_id=item.caller_id)
        store.branch_id.return_value, store.active_for_call.return_value = item.branch_id, None
        when = datetime.combine(item.appointment_date, item.start_time, IST).isoformat()
        state = CallState(call_sid="voice-lab-ambiguous-booking", stage=ConversationStage.CLOSE,
                          accepted_slot=when, slot_status="accepted")
        booking = BookingSession(service, state)
        await booking.restore()
        conversation = TextConversationService(MockLLMProvider(response="नमस्ते।"), booking_session=booking)
        conversation._booking_restored = True
        guard = TurnEpoch()

        async def reserve(value):
            store.get.return_value = store.active_for_call.return_value = value
            guard.advance()
            return value

        store.reserve.side_effect = reserve
        with pytest.raises(asyncio.CancelledError), epoch_scope(guard, guard.snapshot()):
            await conversation.turn(state, "yes", now=NOW)
        committed = store.get.return_value
        with epoch_scope(guard, guard.snapshot()):
            await conversation.turn(state, "stop calling me", now=NOW)
        assert state.appointment_id == str(committed.id)
        assert state.locked_slot == when and state.slot_status == "locked"
        assert state.lead_wants_out and store.active_for_call.await_count == 2
        store.reserve.assert_awaited_once()

    asyncio.run(run())
