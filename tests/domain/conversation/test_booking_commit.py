import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roma.domain.appointments.booking import BookingError
from roma.domain.appointments.slots import TimeSlot
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.prompts import assemble_system_prompt, cache_prefix
from roma.domain.conversation.shortcircuit import canned_reply
from roma.domain.conversation.state import CallState
from roma.domain.conversation.turn import advance_turn
from roma.workers.postcall.job import OUTCOME_NO_LOCK, outcome_for

NOW = datetime(2026, 10, 17, 9, tzinfo=IST)
SLOT = (NOW + timedelta(days=1, hours=2)).isoformat()


def state(**values):
    return CallState(
        booking_required=True,
        stage="close",
        accepted_slot=SLOT,
        slot_status="accepted",
        **values,
    )


async def extraction(*args, **kwargs):
    return TimeSlot(readback_confirmed=True, confidence=0.99)


def test_confirmation_is_persisted_before_win_and_checkpoint():
    async def run():
        current = state()
        key = str(uuid4())
        entered, finish = asyncio.Event(), asyncio.Event()

        async def commit(iso):
            assert iso == SLOT
            assert current.locked_slot is None and current.appointment_id is None
            entered.set()
            await finish.wait()
            return key

        task = asyncio.create_task(
            advance_turn(
                current,
                "haan theek hai",
                client=object(),
                now=NOW,
                extract_time=extraction,
                confirm_booking=commit,
            )
        )
        await entered.wait()
        assert current.locked_slot is None and outcome_for(current) == OUTCOME_NO_LOCK
        finish.set()
        result = await task
        assert result.win and current.appointment_id == key and current.locked_slot == SLOT
        assert "confirm ho gayi" in canned_reply(current, "haan")
        restored = CallState.from_dict(current.to_dict())
        assert restored.appointment_id == key and restored.booking_required

    asyncio.run(run())


@pytest.mark.parametrize("code", ["APPOINTMENT_SLOT_UNAVAILABLE", "BOOKING_UNAVAILABLE"])
def test_failure_keeps_call_unconfirmed_and_does_not_win(code):
    async def run():
        current = state()
        confirm = AsyncMock(side_effect=BookingError(code))
        result = await advance_turn(
            current,
            "haan theek hai",
            client=object(),
            now=NOW,
            extract_time=extraction,
            confirm_booking=confirm,
        )
        assert not result.win and current.locked_slot is None and current.appointment_id is None
        assert current.accepted_slot is None and current.booking_error == code
        assert "confirm ho gayi" not in canned_reply(current, "haan")
        assert outcome_for(current) == OUTCOME_NO_LOCK

    asyncio.run(run())


def test_cancellation_during_commit_cannot_leave_a_false_lock():
    async def run():
        current = state()
        entered = asyncio.Event()

        async def commit(iso):
            entered.set()
            await asyncio.sleep(10)

        task = asyncio.create_task(
            advance_turn(
                current,
                "haan theek hai",
                client=object(),
                now=NOW,
                extract_time=extraction,
                confirm_booking=commit,
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert current.locked_slot is None and current.appointment_id is None

    asyncio.run(run())


def test_unavailable_caller_time_is_not_accepted():
    async def run():
        current = CallState(booking_required=True, stage="pivot")
        calendar = AsyncMock()
        calendar.is_open.return_value = False
        calendar.offers.return_value = []

        async def pick(*args, **kwargs):
            return TimeSlot(accepted=True, day_offset=1, hour=11, confidence=0.99)

        result = await advance_turn(
            current,
            "kal gyaarah baje",
            client=object(),
            now=NOW,
            extract_time=pick,
            calendar=calendar,
        )
        assert not result.win and current.accepted_slot is None and current.stage == "pivot"

    asyncio.run(run())


def test_production_prompt_uses_database_policy_and_static_cache_prefix():
    current = state()
    before = cache_prefix(current.as_prompt_vars())
    prompt = assemble_system_prompt(current.as_prompt_vars(), current.stage)
    assert "There are no fixed slots" not in prompt
    assert 'nothing is "booked out"' not in prompt
    assert "successful database commit" in prompt
    assert "confirmation comes on WhatsApp" not in prompt
    current.appointment_id, current.locked_slot, current.slot_status = (
        str(uuid4()),
        SLOT,
        "locked",
    )
    assert cache_prefix(current.as_prompt_vars()) == before


@pytest.mark.parametrize("text", ["fees kitni hai?", "ji fees batao", "nahi", "maybe yes"])
def test_extractor_cannot_invent_caller_affirmation(text):
    async def run():
        current = state()
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(
            current,
            text,
            client=object(),
            now=NOW,
            extract_time=extraction,
            confirm_booking=confirm,
        )
        assert not result.win and current.appointment_id is None
        confirm.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("text", ["yes", "haan theek hai", "हाँ ठीक है", "હા બરાબર"])
def test_plain_affirmation_is_understood_in_supported_input_languages(text):
    async def run():
        current = state()
        result = await advance_turn(
            current,
            text,
            client=object(),
            now=NOW,
            extract_time=extraction,
            confirm_booking=AsyncMock(return_value=str(uuid4())),
        )
        assert result.win and current.appointment_id

    asyncio.run(run())


def test_failed_confirmation_proposes_only_new_verified_slots():
    async def run():
        current = state()
        calendar = AsyncMock()
        replacement = NOW + timedelta(days=2, hours=3)
        calendar.offers.return_value = [replacement]
        result = await advance_turn(
            current,
            "haan",
            now=NOW,
            client=object(),
            extract_time=extraction,
            calendar=calendar,
            confirm_booking=AsyncMock(side_effect=BookingError("APPOINTMENT_SLOT_UNAVAILABLE")),
        )
        assert not result.win and current.stage == "pivot"
        assert current.slots_offered == [replacement.isoformat()]
        line = canned_reply(current, "haan")
        from roma.domain.conversation.state import spoken_slot

        assert spoken_slot(replacement.isoformat()) in line
        assert line.startswith("Abhi wo time final nahi hua hai. ")
        assert current.appointment_id is None

    asyncio.run(run())


def test_missing_booking_calendar_never_offers_static_times():
    async def run():
        current = CallState(booking_required=True, stage="pivot")
        await advance_turn(current, "visit", now=NOW)
        assert current.slots_offered == [] and current.booking_error == "BOOKING_UNAVAILABLE"

    asyncio.run(run())


def test_production_refresh_keeps_the_callers_requested_day():
    async def run():
        current = CallState(booking_required=True, stage="pivot", pending_day="2026-10-19")
        calendar = AsyncMock()
        calendar.offers.return_value = []
        await advance_turn(current, "visit", now=NOW, calendar=calendar)
        from datetime import date

        calendar.offers.assert_awaited_once_with(NOW, on=date(2026, 10, 19))

    asyncio.run(run())


@pytest.mark.parametrize("text", [
    "Yes, please confirm it.",
    "Yes, that is correct. Confirm it.",
    "Yes, that is correct.",
    "Yes, please book it.",
    "हाँ, पुष्टि कर दीजिए।",
    "હા, કન્ફર્મ કરી દો.",
])
def test_natural_explicit_consent_commits_after_the_readback(text):
    async def run():
        current = state()
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    extract_time=extraction, confirm_booking=confirm)
        assert result.win and current.appointment_id
        confirm.assert_awaited_once_with(SLOT)
    asyncio.run(run())


@pytest.mark.parametrize("text", [
    "Yes, is that correct", "Yes, that is not correct", "Yes, please confirm it?",
    "Yes, please confirm tomorrow at four", "Maybe yes, please confirm it",
])
def test_natural_consent_does_not_accept_questions_negations_or_revisions(text):
    async def run():
        current = state()
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    extract_time=extraction, confirm_booking=confirm)
        assert not result.win and current.appointment_id is None
        confirm.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["pivot", "close"])
def test_natural_consent_cannot_create_a_booking_without_a_selected_readback(stage):
    async def run():
        current = CallState(booking_required=True, stage=stage)
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, "Yes, please confirm it.", client=object(),
                                    now=NOW, extract_time=extraction, confirm_booking=confirm)
        assert not result.win and current.appointment_id is None
        confirm.assert_not_awaited()
    asyncio.run(run())


def test_explicit_readback_confirmation_does_not_depend_on_llm_extraction():
    async def run():
        current = state()
        extract = AsyncMock(side_effect=AssertionError("Confirmation must be deterministic"))
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, "Yes, please confirm it.", client=object(),
                                    now=NOW, extract_time=extract, confirm_booking=confirm)
        assert result.win and current.appointment_id
        extract.assert_not_awaited()
        confirm.assert_awaited_once_with(SLOT)
    asyncio.run(run())


@pytest.mark.parametrize('text', [
    'हाँ सही रहेगा', 'हाँ ये सही है', 'ये सही है', 'हाँ, यह सही रहेगा।',
])
def test_native_hindi_grammatical_consent_uses_selected_readback_without_extraction(text):
    async def run():
        current = state()
        extract = AsyncMock(side_effect=AssertionError('Consent must stay deterministic'))
        key = str(uuid4())
        confirm = AsyncMock(return_value=key)
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    extract_time=extract, confirm_booking=confirm)
        assert result.win and current.appointment_id == key and current.locked_slot == SLOT
        extract.assert_not_awaited()
        confirm.assert_awaited_once_with(SLOT)
    asyncio.run(run())


@pytest.mark.parametrize('text', [
    'नहीं ये सही नहीं है', 'हाँ ये सही रहेगा?', 'हाँ ये शायद सही रहेगा',
    'हाँ ये सही रहेगा लेकिन कल पाँच बजे', 'हाँ ये अज्ञात सही रहेगा',
    'हाई ये सही', 'हाँ साही रहेगा',
])
def test_hindi_grammar_does_not_turn_negation_revision_or_asr_unknown_into_consent(text):
    async def run():
        current = state()
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    extract_time=extraction, confirm_booking=confirm)
        assert not result.win and current.appointment_id is None and current.locked_slot is None
        confirm.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['pivot', 'close'])
def test_native_hindi_consent_cannot_book_without_selected_readback(stage):
    async def run():
        current = CallState(booking_required=True, stage=stage)
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, 'हाँ ये सही है', client=object(), now=NOW,
                                    extract_time=extraction, confirm_booking=confirm)
        assert not result.win and current.appointment_id is None and current.locked_slot is None
        confirm.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('text', [
    'हाँ ये सही है कन्फर्म कीजिए', 'हाँ बुक कर दीजिए',
    'हाँ कन्फर्म कीजिए', 'कन्फर्म कीजिए', 'कंफर्म कीजिये',
    'कंफर्म कीजिए', 'कन्फर्म कीजिये', 'हाँ, बुक कर दीजिये।',
    'હા બરાબર છે કન્ફર્મ કરો',
])
def test_clear_native_confirmation_command_only_accepts_selected_readback(text):
    async def run():
        current = state()
        extract = AsyncMock(side_effect=AssertionError('Explicit consent needs no provider'))
        key = str(uuid4())
        confirm = AsyncMock(return_value=key)
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    extract_time=extract, confirm_booking=confirm)
        assert result.win and current.appointment_id == key
        extract.assert_not_awaited()
        confirm.assert_awaited_once_with(SLOT)
        bare = CallState(booking_required=True, stage='close')
        unselected = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(bare, text, client=object(), now=NOW,
                                    extract_time=extraction, confirm_booking=unselected)
        assert not result.win
        unselected.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('text', [
    'हाई', 'हाई ये सही है', 'हाई ये सही है कन्फर्म कीजिए', 'हाई नहीं ये सही है', 'हाई ये सही है लेकिन कल पांच बजे',
    'हाई ये सही है अहमदाबाद', 'हाई ये सही है?', 'हाई शायद ये सही है',
    'हाई ये साही', 'हाई ये सही है बदलो', 'હા બરાબર નથી કન્ફર્મ કરો',
])
def test_greeting_variant_cannot_hide_revision_negation_or_missing_explicit_clause(text):
    async def run():
        current = state()
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    extract_time=extraction, confirm_booking=confirm)
        assert not result.win
        confirm.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('mark', ['？','﹖','؟'])
def test_unicode_question_mark_never_authorizes_a_readback_commit(mark):
    async def run():
        current = state()
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, 'कन्फर्म कीजिए' + mark,
            client=object(), now=NOW, extract_time=extraction, confirm_booking=confirm)
        assert not result.win
        confirm.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('text', [
    'नहीं कंफर्म कीजिए', 'कन्फर्म कीजिये?', 'कंफर्म कीजिए？',
    'हाँ बुक कर दीजिये कल पाँच बजे', 'हाँ बुक कर दीजिये लेकिन अहमदाबाद',
    'शायद हाँ बुक कर दीजिये', 'हाँ अज्ञात बुक कर दीजिये',
    'हाई बुक कर दीजिये', 'ાબરબર છે, કાંરમ કરો.',
])
def test_orthographic_commands_cannot_hide_refusal_revision_or_corrupted_input(text):
    async def run():
        current = state()
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    extract_time=extraction, confirm_booking=confirm)
        assert not result.win and current.appointment_id is None and current.locked_slot is None
        confirm.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("text", ["नहीं समझ आया", "nahi samjha", "नहीं सुनाई दिया", "nahi dobara boliye"])
def test_clarification_preserves_pending_readback_without_release_extraction_or_commit(text):
    async def run():
        current = state(slots_offered=[SLOT])
        release = AsyncMock()
        extract = AsyncMock(return_value=TimeSlot(accepted=True, chose_offer=1, confidence=1))
        confirm = AsyncMock(return_value=str(uuid4()))
        result = await advance_turn(current, text, client=object(), now=NOW,
                                    release_holds=release, extract_time=extract,
                                    confirm_booking=confirm)
        assert current.accepted_slot == SLOT and current.slot_status == 'accepted'
        assert current.slots_offered == [SLOT]
        assert not result.win and current.appointment_id is None and current.locked_slot is None
        release.assert_not_awaited()
        extract.assert_not_awaited()
        confirm.assert_not_awaited()
        assert '11:00 AM' in canned_reply(current, 'please repeat')
    asyncio.run(run())



def test_genuine_refusal_still_releases_pending_readback_and_correction_still_revises_it():
    async def run():
        current = state(slots_offered=[SLOT])
        release, confirm = AsyncMock(), AsyncMock()
        async def empty(*a, **k):
            return TimeSlot()
        result = await advance_turn(current, 'nahi', client=object(), now=NOW,
                                    release_holds=release, extract_time=empty, confirm_booking=confirm)
        assert not result.win and current.accepted_slot is None and current.slots_offered == []
        release.assert_awaited_once()
        confirm.assert_not_awaited()
        current = state(slots_offered=[SLOT])
        release.reset_mock()
        calendar = AsyncMock()
        calendar.is_open.return_value = True
        async def revised(*a, **k):
            return TimeSlot(day_offset=1, hour=15, accepted=True, confidence=.99)
        result = await advance_turn(current, 'nahi kal 3 baje', client=object(), now=NOW,
                                    release_holds=release, extract_time=revised, confirm_booking=confirm,
                                    calendar=calendar)
        assert current.accepted_slot == (NOW + timedelta(days=1, hours=6)).isoformat()
        assert current.slot_status == 'accepted' and not result.win
        release.assert_not_awaited()
        confirm.assert_not_awaited()
    asyncio.run(run())
