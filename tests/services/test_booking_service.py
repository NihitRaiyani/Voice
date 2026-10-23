import asyncio
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from roma.domain.appointments.booking import BookingError, HoldOwner, SlotKey
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.state import CallState
from roma.domain.persistence import (
    AppointmentRecord,
    PersistenceConflict,
    PersistenceUnavailable,
)
from roma.services.booking_service import BookingService
from roma.services.booking_session import BookingSession

NOW = datetime(2026, 10, 17, 9, tzinfo=IST)


def record(**values):
    return AppointmentRecord(
        **dict(
            id=uuid4(),
            branch_id=uuid4(),
            appointment_date=NOW.date() + timedelta(days=1),
            start_time=time(11),
            status="confirmed",
            created_at=NOW,
            updated_at=NOW,
            **values,
        )
    )


def setup():
    store, holds = AsyncMock(), AsyncMock()
    store.get.return_value = None
    store.available.return_value = True
    store.call.return_value = object()
    holds.owner.return_value = None
    holds.acquire.return_value = 90000
    store.reserve.side_effect = lambda record: record
    return BookingService(store, holds, now=lambda: NOW), store, holds


def test_database_authority_after_lost_hold_and_release_failure():
    async def run():
        service, store, holds = setup()
        item = record(call_id=uuid4())
        owner = HoldOwner(item.call_id, uuid4())
        holds.release.side_effect = RedisConnectionError("unavailable")
        saved = await service.confirm(item, owner=owner)
        assert saved == item
        store.reserve.assert_awaited_once_with(item)
        holds.owner.side_effect = RedisConnectionError("unavailable")
        assert await service.confirm(item, owner=owner) == item

    asyncio.run(run())


def test_foreign_hold_conflict_and_database_failure():
    async def run():
        service, store, holds = setup()
        item = record(call_id=uuid4())
        holds.owner.return_value = HoldOwner(uuid4(), uuid4()).value
        with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
            await service.confirm(item)
        store.reserve.assert_not_awaited()
        holds.owner.return_value = None
        store.reserve.side_effect = PersistenceUnavailable("down")
        with pytest.raises(BookingError, match="BOOKING_UNAVAILABLE"):
            await service.confirm(item)
        holds.release.assert_not_awaited()
        store.reserve.side_effect = PersistenceConflict("lost race")
        with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
            await service.confirm(item)

    asyncio.run(run())


def test_offer_rechecks_after_redis_and_releases_changed_slot():
    async def run():
        service, store, holds = setup()
        store.available.side_effect = [True, False]
        item = record()
        key = SlotKey(item.branch_id, item.appointment_date, item.start_time)
        owner = HoldOwner(uuid4(), uuid4())
        with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
            await service.acquire(key, owner)
        holds.release.assert_awaited_once_with(key, owner)
        holds.acquire.side_effect = RedisConnectionError("down")
        store.available.side_effect = None
        with pytest.raises(BookingError, match="HOLD_UNAVAILABLE"):
            await service.acquire(key, owner)

    asyncio.run(run())


def test_window_and_timezones_are_explicit():
    service, _, _ = setup()
    item = record()
    key = SlotKey(item.branch_id, item.appointment_date, item.start_time)
    assert service.eligible(key)
    for day, at in [
        (NOW.date(), time(10)),
        (key.appointment_date, time(18)),
        (NOW.date() + timedelta(days=15), time(11)),
        (key.appointment_date, time(11, tzinfo=IST)),
    ]:
        assert not service.eligible(SlotKey(key.branch_id, day, at))


def test_timeouts_cancel_storage_operation_and_never_claim_success():
    async def run():
        service, store, holds = setup()
        cancelled = asyncio.Event()

        async def wait(_):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        store.reserve.side_effect = wait
        service.db_timeout = 0.01
        with pytest.raises(BookingError, match="BOOKING_UNAVAILABLE"):
            await service.confirm(record())
        assert cancelled.is_set()
        holds.release.assert_not_awaited()

    asyncio.run(run())


def test_idempotent_retry_overlapping_the_first_commit_returns_the_booking():
    async def run():
        service, store, _ = setup()
        item = record()
        store.get.side_effect = [None, item]
        store.available.return_value = False
        assert await service.confirm(item) == item
        store.reserve.assert_not_awaited()

    asyncio.run(run())


def test_native_postgres_capacity_failure_never_claims_booking_success():
    from asyncpg.exceptions import TooManyConnectionsError

    async def run():
        service, store, holds = setup()
        store.reserve.side_effect = TooManyConnectionsError("private database capacity")
        with pytest.raises(BookingError, match="BOOKING_UNAVAILABLE"):
            await service.confirm(record())
        holds.release.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("outage", ["connection", "timeout", "disabled"])
def test_session_can_offer_accept_and_confirm_using_sql_during_hold_outage(outage):
    async def run():
        service, store, holds = setup()
        item = record(call_id=uuid4(), caller_id=uuid4())
        store.call.return_value = SimpleNamespace(id=item.call_id, caller_id=item.caller_id)
        store.branch_id.return_value = item.branch_id
        store.candidates.return_value = [item]
        if outage == "disabled":
            service.holds = None
        else:
            failure = RedisConnectionError("down") if outage == "connection" else TimeoutError()
            holds.acquire.side_effect = failure
            holds.owner.side_effect = failure
        state = CallState(call_sid="booking-outage-test")
        session = BookingSession(service, state)
        when = datetime.combine(item.appointment_date, item.start_time, IST)

        assert await session.offers(NOW) == [when]
        assert await session.is_open(when)
        assert state.appointment_id is None and state.locked_slot is None
        # SQL availability does not manufacture an advisory lease or a confirmed appointment.
        holds.release.assert_not_awaited()
        saved = await session.confirm(when.isoformat())
        assert saved == str(store.reserve.await_args.args[0].id)
        store.reserve.assert_awaited_once()
        if outage == "disabled":
            holds.release.assert_not_awaited()
        else:
            # Cleanup uses the attempted owner token, without claiming acquire succeeded.
            holds.release.assert_awaited_once_with(*holds.acquire.await_args.args)

    asyncio.run(run())


def test_session_does_not_fall_back_when_another_caller_owns_the_hold():
    async def run():
        service, store, holds = setup()
        item = record(call_id=uuid4(), caller_id=uuid4())
        store.call.return_value = SimpleNamespace(id=item.call_id, caller_id=item.caller_id)
        store.branch_id.return_value = item.branch_id
        store.candidates.return_value = [item]
        holds.acquire.return_value = 0
        holds.owner.return_value = HoldOwner(uuid4(), uuid4()).value
        session = BookingSession(service, CallState(call_sid="booking-conflict-test"))
        when = datetime.combine(item.appointment_date, item.start_time, IST)
        assert await session.offers(NOW) == []
        assert not await session.is_open(when)
        with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
            await session.confirm(when.isoformat())
        store.reserve.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["offers", "is_open"])
@pytest.mark.parametrize("sql_result", [False, PersistenceUnavailable("down")])
def test_hold_outage_requires_a_fresh_successful_sql_availability_check(operation, sql_result):
    async def run():
        service, store, holds = setup()
        item = record(call_id=uuid4(), caller_id=uuid4())
        store.call.return_value = SimpleNamespace(id=item.call_id, caller_id=item.caller_id)
        store.branch_id.return_value = item.branch_id
        store.candidates.return_value = [item]
        # The slot may be booked, or SQL may fail, while Redis is being attempted.
        store.available.side_effect = [True, sql_result]
        holds.acquire.side_effect = RedisConnectionError("down")
        session = BookingSession(service, CallState(call_sid="booking-outage-test"))
        when = datetime.combine(item.appointment_date, item.start_time, IST)
        result = session.offers(NOW) if operation == "offers" else session.is_open(when)
        if isinstance(sql_result, PersistenceUnavailable):
            with pytest.raises(BookingError, match="BOOKING_UNAVAILABLE"):
                await result
        else:
            assert await result == ([] if operation == "offers" else False)
        store.reserve.assert_not_awaited()

    asyncio.run(run())


def test_sql_conflict_after_an_unheld_offer_never_confirms_the_appointment():
    async def run():
        service, store, holds = setup()
        item = record(call_id=uuid4(), caller_id=uuid4())
        store.call.return_value = SimpleNamespace(id=item.call_id, caller_id=item.caller_id)
        store.branch_id.return_value = item.branch_id
        store.candidates.return_value = [item]
        holds.acquire.side_effect = holds.owner.side_effect = RedisConnectionError("down")
        store.reserve.side_effect = PersistenceConflict("another caller committed")
        state = CallState(call_sid="booking-outage-test")
        session = BookingSession(service, state)
        offered = await session.offers(NOW)
        with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
            await session.confirm(offered[0].isoformat())
        assert state.appointment_id is None and state.locked_slot is None
        holds.release.assert_not_awaited()

    asyncio.run(run())


def test_session_recognizes_its_hold_if_acquire_reply_is_lost_then_redis_recovers():
    async def run():
        service, store, holds = setup()
        item = record(call_id=uuid4(), caller_id=uuid4())
        store.call.return_value = SimpleNamespace(id=item.call_id, caller_id=item.caller_id)
        store.branch_id.return_value = item.branch_id
        store.candidates.return_value = [item]
        async def lost_reply(key, owner):
            # The write committed in Redis, but its response did not reach the caller.
            holds.owner.return_value = owner.value
            raise RedisConnectionError("response lost")
        holds.acquire.side_effect = lost_reply
        session = BookingSession(service, CallState(call_sid="booking-outage-test"))
        offered = await session.offers(NOW)
        saved = await session.confirm(offered[0].isoformat())
        assert saved == str(store.reserve.await_args.args[0].id)
        store.reserve.assert_awaited_once()

    asyncio.run(run())
