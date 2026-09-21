from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from alembic import command
from roma.domain.persistence import (
    AppointmentRecord,
    AppointmentSlotRecord,
    AuditLogRecord,
    CallCostRecord,
    CallerRecord,
    CallEventRecord,
    CallRecord,
    CallTurnRecord,
    FollowupJobRecord,
    PersistenceConflict,
    ProviderUsageRecord,
    RecordingRecord,
    SafetyEventRecord,
)
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url

__all__ = ["alembic_config", "disposable_postgres_url"]

pytestmark = pytest.mark.postgres


@pytest.fixture()
def session_factory(alembic_config, disposable_postgres_url):
    command.upgrade(alembic_config, "head")
    engine = create_async_engine(disposable_postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    yield factory

    asyncio.run(engine.dispose())
    command.downgrade(alembic_config, "base")


def _now(offset: int = 0) -> datetime:
    return datetime(2026, 9, 21, 8, 0, tzinfo=UTC) + timedelta(minutes=offset)


async def _seed_reference_data(uow: PostgresUnitOfWork) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    institute_id = await uow.reference_data.upsert_institute(
        code=f"weltec-{uuid4()}",
        name="Weltec Institute",
        is_active=True,
    )
    branch_id = await uow.reference_data.upsert_branch(
        institute_id=institute_id,
        code="ahm",
        name="Ahmedabad",
        city="Ahmedabad",
        timezone="Asia/Kolkata",
        is_active=True,
    )
    course_id = await uow.reference_data.upsert_course(
        institute_id=institute_id,
        code="gd",
        name="Graphic Design",
        description="Design diploma",
        is_active=True,
    )
    assert await uow.reference_data.upsert_branch_course_offering(
        branch_id=branch_id,
        course_id=course_id,
    ) == (branch_id, course_id)

    role_id = await uow.reference_data.upsert_role(name="counsellor", description="Counsellor")
    user_id = await uow.reference_data.upsert_user(
        email=f"{uuid4()}@example.com",
        display_name="Counsellor A",
        password_hash="hash",
        is_active=True,
    )
    assert await uow.reference_data.upsert_user_role(user_id=user_id, role_id=role_id) == (
        user_id,
        role_id,
    )
    counsellor_id = await uow.reference_data.upsert_counsellor(
        branch_id=branch_id,
        employee_code=f"emp-{uuid4()}",
        display_name="Counsellor A",
        user_id=user_id,
        is_active=True,
    )
    return institute_id, branch_id, course_id, user_id, counsellor_id


async def _add_caller_and_call(
    session_factory,
) -> tuple[CallerRecord, CallRecord]:
    async with PostgresUnitOfWork(session_factory) as uow:
        caller = await uow.callers.add(
            CallerRecord(
                id=uuid4(),
                phone_hash=f"hash-{uuid4()}",
                name="Nihi",
                preferred_language="en-IN",
                city="Ahmedabad",
                education="12th",
                current_status="interested",
                created_at=_now(),
                updated_at=_now(),
            )
        )
        call = await uow.calls.add(
            CallRecord(
                id=uuid4(),
                caller_id=caller.id,
                direction="outbound",
                status="pending",
                language="en-IN",
                started_at=_now(1),
                created_at=_now(1),
                updated_at=_now(1),
            )
        )
        await uow.commit()
        return caller, call


def test_repositories_round_trip_durable_records(session_factory):
    async def run() -> None:
        async with PostgresUnitOfWork(session_factory) as uow:
            _, branch_id, course_id, user_id, counsellor_id = await _seed_reference_data(uow)

            phone_hash = f"phone-hash-{uuid4()}"
            caller = await uow.callers.add(
                CallerRecord(
                    id=uuid4(),
                    phone_hash=phone_hash,
                    name="Original",
                    preferred_language="hi-IN",
                    city="Ahmedabad",
                    education="12th",
                    current_status="new",
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            upserted = await uow.callers.add(
                CallerRecord(
                    id=uuid4(),
                    phone_hash=phone_hash,
                    name="Updated",
                    preferred_language="gu-IN",
                    city="Vadodara",
                    education="Diploma",
                    current_status="qualified",
                    created_at=_now(1),
                    updated_at=_now(1),
                )
            )
            assert upserted.id == caller.id
            assert upserted.name == "Updated"

            call = await uow.calls.add(
                CallRecord(
                    id=uuid4(),
                    caller_id=caller.id,
                    provider_call_id=None,
                    direction="outbound",
                    status="pending",
                    language="gu-IN",
                    started_at=_now(2),
                    created_at=_now(2),
                    updated_at=_now(2),
                )
            )
            call = await uow.calls.set_provider_call_id(call.id, "provider-call-1")
            assert call.provider_call_id == "provider-call-1"

            call = await uow.calls.update_lifecycle(
                call.id,
                status="in_progress",
                occurred_at=_now(3),
            )
            assert call.answered_at == _now(3)
            call = await uow.calls.update_lifecycle(
                call.id,
                status="completed",
                occurred_at=_now(4),
                final_stage="booked",
                booking_status="booked",
            )
            assert call.ended_at == _now(4)
            assert call.final_stage == "booked"

            second_turn = await uow.calls.add_turn(
                CallTurnRecord(
                    id=uuid4(),
                    call_id=call.id,
                    turn_number=2,
                    speaker="agent",
                    transcript="Please visit at 10:00.",
                    conversation_stage="booking",
                    route="confirm",
                    latency_ms=300,
                    created_at=_now(6),
                )
            )
            first_turn = await uow.calls.add_turn(
                CallTurnRecord(
                    id=uuid4(),
                    call_id=call.id,
                    turn_number=1,
                    speaker="caller",
                    transcript="I want course details.",
                    conversation_stage="discovery",
                    route="collect",
                    latency_ms=200,
                    created_at=_now(5),
                )
            )
            assert [turn.id for turn in await uow.calls.list_turns(call.id)] == [
                first_turn.id,
                second_turn.id,
            ]

            event = await uow.calls.add_event(
                CallEventRecord(
                    id=uuid4(),
                    call_id=call.id,
                    event_type="stage.changed",
                    conversation_stage="booking",
                    payload={"from": "discovery", "to": "booking"},
                    occurred_at=_now(7),
                    idempotency_key=f"event-{uuid4()}",
                )
            )
            assert event.payload["to"] == "booking"

            slot = await uow.appointments.add_slot(
                AppointmentSlotRecord(
                    id=uuid4(),
                    branch_id=branch_id,
                    appointment_date=date(2026, 9, 22),
                    start_time=time(10, 0),
                    end_time=time(10, 30),
                    capacity=1,
                    status="available",
                    created_at=_now(8),
                    updated_at=_now(8),
                )
            )
            later_slot = await uow.appointments.add_slot(
                AppointmentSlotRecord(
                    id=uuid4(),
                    branch_id=branch_id,
                    appointment_date=date(2026, 9, 22),
                    start_time=time(11, 0),
                    end_time=time(11, 30),
                    capacity=1,
                    status="available",
                    created_at=_now(9),
                    updated_at=_now(9),
                )
            )
            assert [slot.id for slot in await uow.appointments.list_available_slots(
                branch_id,
                date(2026, 9, 22),
            )] == [slot.id, later_slot.id]

            appointment = await uow.appointments.book(
                AppointmentRecord(
                    id=uuid4(),
                    caller_id=caller.id,
                    call_id=call.id,
                    branch_id=branch_id,
                    appointment_date=slot.appointment_date,
                    start_time=slot.start_time,
                    counsellor_id=counsellor_id,
                    course_id=course_id,
                    status="booked",
                    created_at=_now(10),
                    updated_at=_now(10),
                )
            )
            assert appointment.status == "booked"
            assert [
                available.id
                for available in await uow.appointments.list_available_slots(
                    branch_id,
                    date(2026, 9, 22),
                )
            ] == [later_slot.id]

            safety = await uow.evidence.add_safety_event(
                SafetyEventRecord(
                    id=uuid4(),
                    call_id=call.id,
                    turn_id=first_turn.id,
                    rule="claims",
                    original_category="guarantee",
                    replacement_type="softened",
                    metadata={"term": "guaranteed job"},
                    retention_until=_now(60),
                    created_at=_now(11),
                )
            )
            usage = await uow.evidence.add_provider_usage(
                ProviderUsageRecord(
                    id=uuid4(),
                    call_id=call.id,
                    turn_id=second_turn.id,
                    provider="openai",
                    service="llm",
                    model="gpt-test",
                    measured_units=Decimal("12.5"),
                    unit_name="tokens",
                    provider_metadata={"cached": False},
                    occurred_at=_now(12),
                    idempotency_key=f"usage-{uuid4()}",
                )
            )
            cost = await uow.evidence.add_call_cost(
                CallCostRecord(
                    id=uuid4(),
                    call_id=call.id,
                    provider_usage_id=usage.id,
                    provider="openai",
                    service="llm",
                    model="gpt-test",
                    quantity=Decimal("12.5"),
                    unit="tokens",
                    unit_price=Decimal("0.0001"),
                    amount=Decimal("0.0013"),
                    currency="USD",
                    pricing_version="2026-09",
                    metadata={"rounded": True},
                    occurred_at=_now(13),
                    idempotency_key=f"cost-{uuid4()}",
                )
            )
            recording = await uow.evidence.add_recording(
                RecordingRecord(
                    id=uuid4(),
                    call_id=call.id,
                    storage_provider="s3",
                    object_key=f"recordings/{uuid4()}.wav",
                    media_type="audio/wav",
                    duration_ms=1200,
                    size_bytes=42,
                    checksum="sha256:abc",
                    status="available",
                    consent_at=_now(14),
                    retention_until=_now(90),
                    created_at=_now(14),
                )
            )
            followup = await uow.evidence.add_followup_job(
                FollowupJobRecord(
                    id=uuid4(),
                    call_id=call.id,
                    appointment_id=appointment.id,
                    job_type="send_reminder",
                    payload={"channel": "sms"},
                    status="pending",
                    attempts=0,
                    available_at=_now(15),
                    created_at=_now(15),
                    updated_at=_now(15),
                    idempotency_key=f"job-{uuid4()}",
                )
            )
            audit = await uow.evidence.add_audit_log(
                AuditLogRecord(
                    id=uuid4(),
                    actor_user_id=user_id,
                    action="appointment.booked",
                    resource_type="appointment",
                    resource_id=appointment.id,
                    request_id="request-1",
                    correlation_id="call-1",
                    metadata={"source": "voice"},
                    occurred_at=_now(16),
                )
            )
            assert safety.metadata["term"] == "guaranteed job"
            assert usage.provider_metadata["cached"] is False
            assert cost.metadata["rounded"] is True
            assert recording.object_key.startswith("recordings/")
            assert followup.payload["channel"] == "sms"
            assert audit.metadata["source"] == "voice"

            await uow.commit()

        async with PostgresUnitOfWork(session_factory) as uow:
            stored_caller = await uow.callers.get_by_phone_hash(phone_hash)
            assert stored_caller is not None
            assert stored_caller.id == caller.id
            assert stored_caller.name == "Updated"

            stored_call = await uow.calls.get(call.id)
            assert stored_call is not None
            assert stored_call.provider_call_id == "provider-call-1"
            assert stored_call.status == "completed"

            stored_appointment = await uow.appointments.get(appointment.id)
            assert stored_appointment is not None
            assert stored_appointment.call_id == call.id

    asyncio.run(run())


def test_duplicate_provider_call_ids_become_conflicts(session_factory):
    async def run() -> None:
        caller, first_call = await _add_caller_and_call(session_factory)
        async with PostgresUnitOfWork(session_factory) as uow:
            second_call = await uow.calls.add(
                CallRecord(
                    id=uuid4(),
                    caller_id=caller.id,
                    direction="outbound",
                    status="pending",
                    started_at=_now(20),
                    created_at=_now(20),
                    updated_at=_now(20),
                )
            )
            await uow.calls.set_provider_call_id(first_call.id, "duplicate-provider-id")
            with pytest.raises(PersistenceConflict):
                await uow.calls.set_provider_call_id(second_call.id, "duplicate-provider-id")

    asyncio.run(run())


def test_duplicate_turn_numbers_become_conflicts(session_factory):
    async def run() -> None:
        _, call = await _add_caller_and_call(session_factory)
        async with PostgresUnitOfWork(session_factory) as uow:
            record = CallTurnRecord(
                id=uuid4(),
                call_id=call.id,
                turn_number=1,
                speaker="caller",
                created_at=_now(21),
            )
            await uow.calls.add_turn(record)
            with pytest.raises(PersistenceConflict):
                await uow.calls.add_turn(
                    CallTurnRecord(
                        id=uuid4(),
                        call_id=call.id,
                        turn_number=1,
                        speaker="agent",
                        created_at=_now(22),
                    )
                )

    asyncio.run(run())


def test_duplicate_append_only_idempotency_keys_become_conflicts(session_factory):
    async def run() -> None:
        _, call = await _add_caller_and_call(session_factory)
        async with PostgresUnitOfWork(session_factory) as uow:
            await uow.calls.add_event(
                CallEventRecord(
                    id=uuid4(),
                    call_id=call.id,
                    event_type="stage.changed",
                    occurred_at=_now(23),
                    idempotency_key="same-event",
                )
            )
            with pytest.raises(PersistenceConflict):
                await uow.calls.add_event(
                    CallEventRecord(
                        id=uuid4(),
                        call_id=call.id,
                        event_type="stage.changed",
                        occurred_at=_now(24),
                        idempotency_key="same-event",
                    )
                )

        async with PostgresUnitOfWork(session_factory) as uow:
            await uow.evidence.add_provider_usage(
                ProviderUsageRecord(
                    id=uuid4(),
                    call_id=call.id,
                    provider="openai",
                    service="llm",
                    measured_units=Decimal("1"),
                    unit_name="tokens",
                    occurred_at=_now(25),
                    idempotency_key="same-usage",
                )
            )
            with pytest.raises(PersistenceConflict):
                await uow.evidence.add_provider_usage(
                    ProviderUsageRecord(
                        id=uuid4(),
                        call_id=call.id,
                        provider="openai",
                        service="llm",
                        measured_units=Decimal("2"),
                        unit_name="tokens",
                        occurred_at=_now(26),
                        idempotency_key="same-usage",
                    )
                )

        async with PostgresUnitOfWork(session_factory) as uow:
            await uow.evidence.add_call_cost(
                CallCostRecord(
                    id=uuid4(),
                    call_id=call.id,
                    provider="openai",
                    service="llm",
                    quantity=Decimal("1"),
                    unit="tokens",
                    unit_price=Decimal("0.1"),
                    amount=Decimal("0.1"),
                    currency="USD",
                    pricing_version="test",
                    occurred_at=_now(27),
                    idempotency_key="same-cost",
                )
            )
            with pytest.raises(PersistenceConflict):
                await uow.evidence.add_call_cost(
                    CallCostRecord(
                        id=uuid4(),
                        call_id=call.id,
                        provider="openai",
                        service="llm",
                        quantity=Decimal("1"),
                        unit="tokens",
                        unit_price=Decimal("0.1"),
                        amount=Decimal("0.1"),
                        currency="USD",
                        pricing_version="test",
                        occurred_at=_now(28),
                        idempotency_key="same-cost",
                    )
                )

        async with PostgresUnitOfWork(session_factory) as uow:
            await uow.evidence.add_followup_job(
                FollowupJobRecord(
                    id=uuid4(),
                    job_type="reminder",
                    payload={},
                    status="pending",
                    attempts=0,
                    available_at=_now(29),
                    created_at=_now(29),
                    updated_at=_now(29),
                    idempotency_key="same-job",
                )
            )
            with pytest.raises(PersistenceConflict):
                await uow.evidence.add_followup_job(
                    FollowupJobRecord(
                        id=uuid4(),
                        job_type="reminder",
                        payload={},
                        status="pending",
                        attempts=0,
                        available_at=_now(30),
                        created_at=_now(30),
                        updated_at=_now(30),
                        idempotency_key="same-job",
                    )
                )

    asyncio.run(run())


def test_duplicate_active_slot_bookings_become_conflicts(session_factory):
    async def run() -> None:
        caller, call = await _add_caller_and_call(session_factory)
        async with PostgresUnitOfWork(session_factory) as uow:
            _, branch_id, _, _, _ = await _seed_reference_data(uow)
            slot = await uow.appointments.add_slot(
                AppointmentSlotRecord(
                    id=uuid4(),
                    branch_id=branch_id,
                    appointment_date=date(2026, 9, 23),
                    start_time=time(10, 0),
                    end_time=time(10, 30),
                    capacity=1,
                    status="available",
                    created_at=_now(31),
                    updated_at=_now(31),
                )
            )
            await uow.appointments.book(
                AppointmentRecord(
                    id=uuid4(),
                    caller_id=caller.id,
                    call_id=call.id,
                    branch_id=branch_id,
                    appointment_date=slot.appointment_date,
                    start_time=slot.start_time,
                    status="booked",
                    created_at=_now(32),
                    updated_at=_now(32),
                )
            )
            with pytest.raises(PersistenceConflict):
                await uow.appointments.book(
                    AppointmentRecord(
                        id=uuid4(),
                        caller_id=caller.id,
                        call_id=call.id,
                        branch_id=branch_id,
                        appointment_date=slot.appointment_date,
                        start_time=slot.start_time,
                        status="confirmed",
                        created_at=_now(33),
                        updated_at=_now(33),
                    )
                )

    asyncio.run(run())


def test_later_invalid_row_rolls_back_earlier_rows(session_factory):
    async def run() -> None:
        phone_hash = f"rollback-{uuid4()}"
        with pytest.raises(PersistenceConflict):
            async with PostgresUnitOfWork(session_factory) as uow:
                caller = await uow.callers.add(
                    CallerRecord(
                        id=uuid4(),
                        phone_hash=phone_hash,
                        created_at=_now(34),
                        updated_at=_now(34),
                    )
                )
                await uow.calls.add(
                    CallRecord(
                        id=uuid4(),
                        caller_id=caller.id,
                        direction="diagonal",
                        status="pending",
                        started_at=_now(35),
                        created_at=_now(35),
                        updated_at=_now(35),
                    )
                )

        async with PostgresUnitOfWork(session_factory) as uow:
            assert await uow.callers.get_by_phone_hash(phone_hash) is None

    asyncio.run(run())
