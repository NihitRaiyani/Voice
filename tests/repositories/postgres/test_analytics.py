"""SQL analytics against an owned random-port database, never configured Roma."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from roma.repositories.postgres.models import Call, CallTurn, SafetyEvent
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.repositories.postgres import test_costs

cost_database = test_costs.cost_database


@pytest.mark.postgres
def test_overview_sql_cohort_latency_languages_and_safety(cost_database):
    from roma.services.analytics_service import AnalyticsService

    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        when = datetime(2031, 4, 5, 12, tzinfo=UTC)
        first, second, third = uuid4(), uuid4(), uuid4()
        try:
            async with factory() as session, session.begin():
                session.add_all(
                    [
                        Call(
                            id=first,
                            direction="inbound",
                            status="completed",
                            language="gu",
                            started_at=when,
                            ended_at=when + timedelta(seconds=60),
                        ),
                        Call(
                            id=second,
                            direction="inbound",
                            status="failed",
                            language="hi",
                            started_at=when,
                            ended_at=when + timedelta(seconds=20),
                        ),
                        Call(
                            id=third,
                            direction="inbound",
                            status="in_progress",
                            language="untrusted",
                            started_at=when,
                        ),
                        Call(
                            direction="outbound",
                            status="completed",
                            language="en",
                            started_at=when - timedelta(days=1),
                        ),
                    ]
                )
                await session.flush()
                session.add_all(
                    [
                        CallTurn(call_id=first, turn_number=1, speaker="agent", latency_ms=100),
                        CallTurn(call_id=first, turn_number=2, speaker="agent", latency_ms=300),
                        CallTurn(
                            call_id=second, turn_number=1, speaker="agent", latency_ms=None
                        ),
                        SafetyEvent(call_id=first, rule="salary"),
                        SafetyEvent(call_id=first, rule="salary"),
                    ]
                )
            report = await AnalyticsService(factory).overview(
                start_date=date(2031, 4, 5), end_date=date(2031, 4, 6)
            )
            assert report["calls"] == {"total": 3, "completed": 1, "abandoned": 1, "active": 1}
            assert report["average_duration_seconds"] == 40
            assert report["average_turns"] == 1
            assert report["latency_ms"] == {
                "samples": 2,
                "missing": 1,
                "average": 200,
                "p50": 200,
                "p95": 290,
                "p99": 298,
            }
            assert report["languages"] == {
                "gu": 1,
                "hi": 1,
                "en": 0,
                "code_mix": 0,
                "unknown": 1,
            }
            assert report["safety_rules"] == [{"rule": "salary", "count": 2}]
            assert report["appointment_conversion_rate"] == 0
            assert report["costs"]["currencies"] == []
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_funnel_requires_ordered_observed_milestones_and_sql_confirmation(cost_database):
    from roma.repositories.postgres.models import CallEvent
    from roma.services.analytics_service import AnalyticsService

    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        when = datetime(2032, 4, 5, 12, tzinfo=UTC)
        ordered, skipped, historical = uuid4(), uuid4(), uuid4()
        stages = (
            "greeting_completed",
            "discovery_completed",
            "value_presented",
            "booking_offered",
            "slot_accepted",
        )
        try:
            async with factory() as session, session.begin():
                session.add_all(
                    [
                        Call(
                            id=value,
                            direction="inbound",
                            status="completed",
                            final_stage="close",
                            booking_status="locked",
                            started_at=when,
                        )
                        for value in (ordered, skipped, historical)
                    ]
                )
                await session.flush()
                session.add_all(
                    [
                        CallEvent(
                            call_id=ordered,
                            event_type=f"analytics.{stage}",
                            occurred_at=when + timedelta(seconds=i + 1),
                        )
                        for i, stage in enumerate(stages)
                    ]
                )
                session.add_all(
                    [
                        CallEvent(
                            call_id=skipped,
                            event_type="analytics.discovery_completed",
                            occurred_at=when + timedelta(seconds=1),
                        ),
                        CallEvent(
                            call_id=skipped,
                            event_type="analytics.greeting_completed",
                            occurred_at=when + timedelta(seconds=2),
                        ),
                        CallEvent(
                            call_id=ordered,
                            event_type="analytics.appointment_confirmed",
                            occurred_at=when + timedelta(seconds=6),
                        ),
                    ]
                )
            report = await AnalyticsService(factory).funnel(start_date=when.date())
            assert report["stages"] == [
                {"stage": name, "calls": count}
                for name, count in zip(
                    ("incoming", *stages, "appointment_confirmed"),
                    (3, 2, 1, 1, 1, 1, 0),
                    strict=True,
                )
            ]
            assert (
                report["evidence"]
                == "ordered persisted milestones; SQL appointment required for confirmation"
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_sql_appointments_branch_unknown_bucket_and_exact_currency_costs(cost_database):
    from datetime import time
    from decimal import Decimal

    from roma.domain.costs.usage import UsageEvent
    from roma.repositories.postgres.models import (
        Appointment,
        AppointmentSlot,
        Branch,
        CallEvent,
        Institute,
    )
    from roma.services.analytics_service import AnalyticsService
    from roma.services.cost_service import CostService

    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        when = datetime(2033, 4, 5, 12, tzinfo=UTC)
        branch, institute, booked, unknown = uuid4(), uuid4(), uuid4(), uuid4()
        try:
            async with factory() as session, session.begin():
                session.add(Institute(id=institute, code=str(institute), name="Synthetic"))
                await session.flush()
                session.add(
                    Branch(
                        id=branch,
                        institute_id=institute,
                        code="test",
                        name="TEST ONLY",
                        city="Synthetic",
                        timezone="Asia/Kolkata",
                    )
                )
                session.add_all(
                    [
                        Call(
                            id=booked, direction="inbound", status="completed", started_at=when
                        ),
                        Call(
                            id=unknown,
                            direction="inbound",
                            status="completed",
                            booking_status="confirmed",
                            started_at=when,
                        ),
                    ]
                )
                await session.flush()
                session.add(
                    AppointmentSlot(
                        branch_id=branch,
                        appointment_date=when.date(),
                        start_time=time(14),
                        end_time=time(15),
                        capacity=1,
                        status="booked",
                    )
                )
                await session.flush()
                session.add(
                    Appointment(
                        call_id=booked,
                        branch_id=branch,
                        appointment_date=when.date(),
                        start_time=time(14),
                        status="booked",
                        created_at=when + timedelta(seconds=7),
                    )
                )
                session.add_all(
                    [
                        CallEvent(
                            call_id=booked,
                            event_type=f"analytics.{stage}",
                            occurred_at=when + timedelta(seconds=i + 1),
                        )
                        for i, stage in enumerate(
                            (
                                "greeting_completed",
                                "discovery_completed",
                                "value_presented",
                                "booking_offered",
                                "slot_accepted",
                            )
                        )
                    ]
                )
            cost = CostService(factory)
            for currency, amount in (("INR", "0.123456"), ("USD", "0.000017")):
                await cost.record(
                    UsageEvent(
                        event_key=f"analytics-{currency}",
                        provider="synthetic",
                        service="telephony",
                        call_id=booked,
                        occurred_at=when,
                        carrier_seconds=Decimal(1),
                        observed_amount=Decimal(amount),
                        observed_currency=currency,
                    )
                )
            service = AnalyticsService(factory)
            report = await service.branches(start_date=when.date())
            assert report["branches"] == [
                {
                    "branch_id": str(branch),
                    "branch_name": "TEST ONLY",
                    "calls": 1,
                    "appointments": 1,
                },
                {"branch_id": None, "branch_name": "unknown", "calls": 1, "appointments": 0},
            ]
            overview = await service.overview(start_date=when.date())
            assert overview["converted_calls"] == 1
            assert overview["appointment_conversion_rate"] == 0.5
            assert [item["average_per_call"] for item in overview["costs"]["currencies"]] == [
                "0.123456",
                "0.000017",
            ]
            assert (await service.funnel(start_date=when.date()))["stages"][-1] == {
                "stage": "appointment_confirmed",
                "calls": 1,
            }
        finally:
            await engine.dispose()

    asyncio.run(run())
