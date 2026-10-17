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
        assert "confirm nahi hua" in line
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
