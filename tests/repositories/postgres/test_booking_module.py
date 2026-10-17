import asyncio
from datetime import datetime, time, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from roma.api.v1.calls import mount_web_api
from roma.core.config import Settings, get_settings
from roma.domain.appointments.booking import BookingError, HoldOwner, SlotKey
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.state import CallState
from roma.domain.persistence import AppointmentRecord, AppointmentSlotRecord, CallRecord
from roma.repositories.postgres.booking import PostgresBookingStore
from roma.repositories.postgres.models import Appointment, AppointmentSlot, Call
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
from roma.repositories.redis.appointment_holds import RedisAppointmentHolds
from roma.schemas.rest_api import CreateAppointmentRequest, UpdateAppointmentRequest
from roma.services.booking_service import BookingService
from roma.services.booking_session import BookingSession
from roma.services.rest_api_service import RestApiService, RestApiServiceError
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from tests.repositories.postgres.test_safety_audit import session_factory
from tests.workers.test_dramatiq_worker import redis_url

__all__ = ["session_factory", "redis_url"]
pytestmark = pytest.mark.postgres
NOW = datetime(2026, 10, 17, 9, tzinfo=IST)


def _run(coroutine, factory):
    async def safely():
        try:
            return await coroutine
        finally:
            await factory.kw["bind"].dispose()

    return asyncio.run(safely())


async def seed(factory):
    async with PostgresUnitOfWork(factory) as uow:
        institute = await uow.reference_data.upsert_institute(
            code="test", name="Test", is_active=True
        )
        branch = await uow.reference_data.upsert_branch(
            institute_id=institute,
            code="test",
            name="Vadodara",
            city="Vadodara",
            timezone="Asia/Kolkata",
            is_active=True,
        )
        slots = []
        for hour in (11, 17):
            slots.append(
                await uow.appointments.add_slot(
                    AppointmentSlotRecord(
                        id=uuid4(),
                        branch_id=branch,
                        appointment_date=NOW.date() + timedelta(days=1),
                        start_time=time(hour),
                        end_time=time(hour, 30),
                        capacity=1,
                        status="available",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            )
        calls = [
            await uow.calls.add(
                CallRecord(
                    id=uuid4(),
                    caller_id=None,
                    provider_call_id=f"test-{uuid4()}",
                    direction="inbound",
                    status="in_progress",
                    started_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            for _ in range(2)
        ]
        await uow.commit()
    return branch, slots, calls


def item(slot, **values):
    return AppointmentRecord(
        **dict(
            id=uuid4(),
            branch_id=slot.branch_id,
            appointment_date=slot.appointment_date,
            start_time=slot.start_time,
            status="confirmed",
            created_at=NOW,
            updated_at=NOW,
            **values,
        )
    )


def test_100_http_requests_have_exactly_one_commit(session_factory, monkeypatch):
    # A clean CI checkout has no owner .env; these HTTP tests never call a provider.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-unused-key")
    monkeypatch.setenv("SARVAM_API_KEY", "offline-unused-key")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("API_TOKEN", "booking-test-token")
    get_settings.cache_clear()

    async def run():
        branch, slots, _ = await seed(session_factory)
        service = BookingService(PostgresBookingStore(session_factory), now=lambda: NOW)
        app = FastAPI()
        app.state.booking_service = service
        mount_web_api(app, session_factory=session_factory)
        payload = dict(
            branch_id=str(branch),
            appointment_date=slots[0].appointment_date.isoformat(),
            start_time="11:00:00",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            headers={"Authorization": "Bearer booking-test-token"},
        ) as client:
            responses = await asyncio.gather(
                *(client.post("/api/v1/appointments", json=payload) for _ in range(100))
            )
        assert [r.status_code for r in responses].count(201) == 1
        assert [r.status_code for r in responses].count(409) == 99
        assert all(
            r.json()["error"]["code"] == "APPOINTMENT_SLOT_UNAVAILABLE"
            for r in responses
            if r.status_code == 409
        )
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Appointment)) == 1
            assert (await session.get(AppointmentSlot, slots[0].id)).status == "booked"

    try:
        _run(run(), session_factory)
    finally:
        get_settings.cache_clear()


def test_retry_cancellation_and_rebooking_are_atomic(session_factory):
    async def run():
        branch, slots, calls = await seed(session_factory)
        booking = BookingService(PostgresBookingStore(session_factory), now=lambda: NOW)
        api = RestApiService(session_factory, booking)
        request = CreateAppointmentRequest(
            branch_id=branch,
            appointment_date=slots[0].appointment_date,
            start_time=time(11),
            call_id=calls[0].id,
            idempotency_key=uuid4(),
        )
        first, retry = await asyncio.gather(
            api.create_appointment(request), api.create_appointment(request)
        )
        assert first["id"] == retry["id"]
        from uuid import UUID

        original_id = UUID(first["id"])
        await api.cancel_appointment(original_id)
        # Explicit new booking identity is required after a terminal appointment.
        request = request.model_copy(update={"idempotency_key": uuid4()})
        second = await api.create_appointment(request)
        assert first["id"] != second["id"]
        await api.cancel_appointment(original_id)  # stale retry cannot unlock successor
        async with session_factory() as session:
            assert (await session.get(AppointmentSlot, slots[0].id)).status == "booked"
            assert (await session.get(Call, calls[0].id)).booking_status == "booked"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Appointment)
                    .where(Appointment.status.in_(("booked", "confirmed")))
                )
                == 1
            )
        with pytest.raises(RestApiServiceError) as caught:
            await api.update_appointment(
                original_id, UpdateAppointmentRequest(status="confirmed")
            )
        assert caught.value.code == "APPOINTMENT_SLOT_UNAVAILABLE"

    _run(run(), session_factory)


def test_commit_failure_rolls_back_slot_and_appointment(session_factory):
    async def run():
        _, slots, _ = await seed(session_factory)
        service = BookingService(PostgresBookingStore(session_factory), now=lambda: NOW)

        engine = session_factory.kw["bind"]

        def fail_commit(connection):
            raise OSError("synthetic commit failure")

        event.listen(engine.sync_engine, "commit", fail_commit)
        try:
            with pytest.raises(BookingError, match="BOOKING_UNAVAILABLE"):
                await service.confirm(item(slots[0]))
        finally:
            event.remove(engine.sync_engine, "commit", fail_commit)
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Appointment)) == 0
            assert (await session.get(AppointmentSlot, slots[0].id)).status == "available"

    _run(run(), session_factory)


def test_database_index_is_final_authority_even_if_flag_is_wrong(session_factory):
    async def run():
        _, slots, _ = await seed(session_factory)
        store = PostgresBookingStore(session_factory)
        first = await store.reserve(item(slots[0]))
        async with session_factory() as session, session.begin():
            (await session.get(AppointmentSlot, slots[0].id)).status = "available"
        with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
            await BookingService(store, now=lambda: NOW).confirm(item(slots[0]))
        # Bypass application checks entirely: the partial unique index still rejects it.
        async with session_factory() as session:
            session.add(
                Appointment(
                    id=uuid4(),
                    branch_id=first.branch_id,
                    appointment_date=first.appointment_date,
                    start_time=first.start_time,
                    status="confirmed",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    _run(run(), session_factory)


def test_real_holds_loss_expiry_and_restore_use_database_truth(session_factory, redis_url):
    async def run():
        _, slots, calls = await seed(session_factory)
        client = Redis.from_url(redis_url)
        try:
            holds = RedisAppointmentHolds(client)
            service = BookingService(
                PostgresBookingStore(session_factory), holds, now=lambda: NOW
            )
            a = BookingSession(
                service, CallState(call_sid=calls[0].provider_call_id, stage="pivot")
            )
            b = BookingSession(
                service, CallState(call_sid=calls[1].provider_call_id, stage="pivot")
            )
            await a.restore()
            await b.restore()
            offered = await a.offers(NOW, count=1)
            other = await b.offers(NOW, count=1)
            assert offered and other and offered[0] != other[0]
            key = SlotKey(slots[0].branch_id, slots[0].appointment_date, slots[0].start_time)
            await client.delete(key.redis_key)  # expired/lost while A thinks
            owner = HoldOwner(calls[1].id, uuid4())
            assert await holds.acquire(key, owner) > 0
            with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
                await a.confirm(offered[0].isoformat())
            await holds.release(key, owner)
            appointment = await a.confirm(
                offered[0].isoformat()
            )  # lost hold allowed after locked SQL recheck
            stale = CallState(call_sid=calls[0].provider_call_id, locked_slot=None)
            restored = BookingSession(service, stale)
            await restored.restore()
            assert (
                stale.appointment_id == appointment
                and stale.locked_slot == offered[0].isoformat()
            )
            fake = CallState(
                call_sid=calls[1].provider_call_id,
                locked_slot=offered[0].isoformat(),
                slot_status="locked",
                appointment_id=str(uuid4()),
            )
            await BookingSession(service, fake).restore()
            assert fake.appointment_id is None and fake.locked_slot is None
            await b.aclose()
        finally:
            await client.aclose()

    _run(run(), session_factory)


def test_call_serialization_and_connections_released_before_redis(session_factory):
    async def run():
        _, slots, calls = await seed(session_factory)
        engine = session_factory.kw["bind"]

        class CheckedHolds:
            async def owner(self, key):
                assert engine.pool.checkedout() == 0
                return None

            async def acquire(self, key, owner):
                assert engine.pool.checkedout() == 0
                return 90000

            async def release(self, key, owner):
                assert engine.pool.checkedout() == 0
                return True

        service = BookingService(
            PostgresBookingStore(session_factory), CheckedHolds(), now=lambda: NOW
        )
        key = SlotKey(slots[0].branch_id, slots[0].appointment_date, slots[0].start_time)
        owner = HoldOwner(calls[0].id, uuid4())
        assert await service.acquire(key, owner) > 0
        await service.confirm(item(slots[0], call_id=calls[0].id), owner=owner)
        assert (
            engine.pool.checkedout() == 0
        )  # caller/model/audio may now wait without a transaction
        with pytest.raises(BookingError, match="APPOINTMENT_SLOT_UNAVAILABLE"):
            await service.confirm(item(slots[1], call_id=calls[0].id))
        assert engine.pool.checkedout() == 0

    _run(run(), session_factory)


def test_simultaneous_different_slots_for_one_call_create_one_appointment(session_factory):
    async def run():
        _, slots, calls = await seed(session_factory)
        service = BookingService(PostgresBookingStore(session_factory), now=lambda: NOW)
        results = await asyncio.gather(
            *(service.confirm(item(slot, call_id=calls[0].id)) for slot in slots),
            return_exceptions=True,
        )
        assert sum(isinstance(result, AppointmentRecord) for result in results) == 1
        assert (
            sum(
                isinstance(result, BookingError)
                and result.code == "APPOINTMENT_SLOT_UNAVAILABLE"
                for result in results
            )
            == 1
        )
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Appointment)) == 1

    _run(run(), session_factory)
