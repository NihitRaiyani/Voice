import asyncio
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from roma.domain.appointments.booking import BookingError, HoldOwner, SlotKey
from roma.domain.appointments.timeresolve import IST
from roma.domain.persistence import (
    AppointmentRecord,
    PersistenceConflict,
    PersistenceUnavailable,
)
from roma.services.booking_service import BookingService

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
