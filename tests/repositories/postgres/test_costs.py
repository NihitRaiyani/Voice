"""Native, owned random-port SQL cost evidence; never uses configured DATABASE_URL."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from roma.core.ai_contracts import LLMGenerationMetrics
from roma.domain.costs.usage import UsageEvent
from roma.domain.persistence import PersistenceConflict
from roma.repositories.postgres.models import Call, CallTurn
from roma.services.cost_service import CostService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.repositories.postgres.test_migrations import _local_postgres_url


@pytest.fixture(scope="module")
def cost_database():
    with _local_postgres_url() as url:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        yield url


@pytest.mark.postgres
def test_concurrent_duplicate_has_one_cost_and_changed_usage_conflicts(cost_database):
    async def run():
        engine = create_async_engine(cost_database)
        service = CostService(async_sessionmaker(engine, expire_on_commit=False))
        usage = UsageEvent(
            event_key="test-carrier-1",
            provider="test-carrier",
            service="telephony",
            occurred_at=datetime(2026, 10, 26, tzinfo=UTC),
            carrier_seconds=Decimal("61.5"),
            observed_amount=Decimal("0.012345"),
            observed_currency="USD",
        )
        try:
            results = await asyncio.gather(*(service.record(usage) for _ in range(8)))
            assert results.count(True) == 1
            with pytest.raises(PersistenceConflict):
                await service.record(replace(usage, carrier_seconds=Decimal(62)))
            report = await service.summary(day=datetime(2026, 10, 26, tzinfo=UTC).date())
            assert report["usage_events"] == 1
            assert report["currencies"][0]["amount"] == "0.012345"
            assert report["units"]["carrier_seconds"] == "61.5"
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_runtime_speech_and_missing_llm_metrics_are_retained_as_unpriced_usage(cost_database):
    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        service = CostService(factory)
        when = datetime(2029, 1, 1, tzinfo=UTC)
        try:
            await service.record_speech(
                "stt",
                "runtime-asr",
                audio_seconds=1.25,
                model="test-asr",
                provider="local",
                occurred_at=when,
            )
            await service.record_speech(
                "tts",
                "runtime-tts",
                audio_seconds=2.5,
                characters=25,
                cache_hit=True,
                model="test-tts",
                provider="local",
                occurred_at=when,
            )
            await service.record_llm(
                None,
                "runtime-cancelled",
                model="claude-haiku-5-5",
                provider="anthropic",
                occurred_at=when,
            )
            report = await service.summary(month="2029-01")
            assert report["usage_events"] == report["unpriced_events"] == 3
            assert report["currencies"] == [] and report["complete"] is False
            assert report["units"]["audio_seconds"] == "3.75"
            assert report["units"]["cache_hits"] == "1"
            assert "gpu_seconds" not in report["units"]
            assert report["unknown_units"]["input_tokens"] == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_call_turn_month_average_currency_and_unpriced_usage_remain_explicit(cost_database):
    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        service = CostService(factory)
        first, second, turn = uuid4(), uuid4(), uuid4()
        when = datetime(2028, 2, 29, 12, tzinfo=UTC)
        try:
            async with factory() as session, session.begin():
                session.add_all(
                    [
                        Call(id=value, direction="inbound", status="completed", started_at=when)
                        for value in (first, second)
                    ]
                )
                await session.flush()
                session.add(CallTurn(id=turn, call_id=first, turn_number=1, speaker="agent"))
            for call, amount, currency in (
                (first, "1", "INR"),
                (second, "3", "INR"),
                (first, "2", "USD"),
            ):
                await service.record(
                    UsageEvent(
                        event_key=f"multicurrency-{call}-{currency}",
                        provider="test-carrier",
                        service="telephony",
                        occurred_at=when,
                        call_id=call,
                        carrier_seconds=Decimal(60),
                        observed_amount=Decimal(amount),
                        observed_currency=currency,
                    )
                )
            await service.record(
                UsageEvent(
                    event_key="unpriced-tts",
                    provider="local",
                    service="tts",
                    call_id=first,
                    turn_id=turn,
                    occurred_at=when,
                    characters=42,
                    audio_seconds=Decimal("1.5"),
                    cache_hits=1,
                )
            )
            await service.record(
                UsageEvent(
                    event_key="unpriced-asr",
                    provider="local",
                    service="stt",
                    call_id=first,
                    turn_id=turn,
                    occurred_at=when,
                    audio_seconds=Decimal("2.5"),
                    gpu_seconds=Decimal("0.5"),
                )
            )
            monthly = await service.summary(month="2028-02")
            assert monthly["usage_events"] == 5 and monthly["unpriced_events"] == 2
            assert monthly["complete"] is False
            assert monthly["units"]["audio_seconds"] == "4"
            assert monthly["units"]["characters"] == "42"
            assert monthly["currencies"] == [
                {
                    "currency": "INR",
                    "amount": "4",
                    "priced_events": 2,
                    "calls_with_priced_usage": 2,
                    "average_per_call": "2",
                },
                {
                    "currency": "USD",
                    "amount": "2",
                    "priced_events": 1,
                    "calls_with_priced_usage": 1,
                    "average_per_call": "2",
                },
            ]
            daily = await service.summary(day=when.date())
            assert daily["currencies"] == monthly["currencies"]
            assert (await service.summary(month="2028-03"))["currencies"] == []
            per_call = await service.summary(call_id=first, group_by="turn")
            assert per_call["usage_events"] == 4
            assert per_call["groups"][0]["turn"] == str(turn)
            assert per_call["groups"][0]["unpriced_events"] == 2
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_production_claude_totals_are_normalized_without_counting_cached_tokens_twice(
    cost_database,
):
    async def run():
        engine = create_async_engine(cost_database)
        service = CostService(async_sessionmaker(engine, expire_on_commit=False))
        final = LLMGenerationMetrics(
            model="claude-haiku-5-5",
            provider="claude",
            ttft_ms=1,
            generation_secs=1,
            attempts=1,
            input_tokens=3000,
            output_tokens=100,
            cached_input_tokens=2000,
            cache_creation_input_tokens=0,
            usage_known=True,
            charged_inr=1,
            cost_inr=1,
            call_tokens_used=3100,
            call_spent_inr=1,
        )
        try:
            await service.record_llm(
                final, "production-counts", occurred_at=datetime(2027, 1, 1, tzinfo=UTC)
            )
            report = await service.summary(month="2027-01", group_by="provider")
            assert report["units"]["input_tokens"] == "1000"
            assert report["currencies"] == [
                {
                    "currency": "USD",
                    "amount": "0.00017",
                    "priced_events": 1,
                    "calls_with_priced_usage": 0,
                    "average_per_call": None,
                }
            ]
            assert report["groups"][0]["provider"] == "anthropic"
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_runtime_usage_helpers_deduplicate_replays_but_reject_changed_measurements(
    cost_database,
):
    async def run():
        engine = create_async_engine(cost_database)
        service = CostService(async_sessionmaker(engine, expire_on_commit=False))
        final = LLMGenerationMetrics(
            model="claude-haiku-5-5",
            provider="claude",
            ttft_ms=1,
            generation_secs=1,
            attempts=1,
            input_tokens=1000,
            output_tokens=100,
            cached_input_tokens=0,
            cache_creation_input_tokens=0,
            usage_known=True,
            charged_inr=1,
            cost_inr=1,
            call_tokens_used=1100,
            call_spent_inr=1,
        )
        request_id = str(uuid4())
        call_id = uuid4()
        try:
            async with service.session_factory() as session, session.begin():
                session.add(
                    Call(
                        id=call_id,
                        direction="inbound",
                        status="completed",
                        started_at=datetime.now(UTC),
                    )
                )
            assert await service.record_llm(final, request_id, call_id=call_id) is True
            assert await service.record_llm(final, request_id, call_id=call_id) is False
            with pytest.raises(PersistenceConflict):
                await service.record_llm(
                    replace(final, output_tokens=101), request_id, call_id=call_id
                )
            assert (
                await service.record_speech(
                    "stt", request_id, audio_seconds=1.25, call_id=call_id
                )
                is True
            )
            assert (
                await service.record_speech(
                    "stt", request_id, audio_seconds=1.25, call_id=call_id
                )
                is False
            )
            with pytest.raises(PersistenceConflict):
                await service.record_speech(
                    "stt", request_id, audio_seconds=1.5, call_id=call_id
                )
            report = await service.summary(call_id=call_id)
            assert report["usage_events"] == 2
            assert report["unpriced_events"] == 1
            assert report["units"]["output_tokens"] == "100"
            assert report["units"]["audio_seconds"] == "1.25"
            assert report["currencies"][0]["amount"] == "0.00015"
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_call_recorder_freezes_turn_at_admission_and_creates_metadata_only_turn(cost_database):
    from types import SimpleNamespace
    from uuid import NAMESPACE_URL, uuid5

    from roma.services.call_costs import CallCostRecorder
    from sqlalchemy import select

    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        call_id, sid = uuid4(), str(uuid4())
        retention = datetime(2035, 1, 1, tzinfo=UTC)
        state = SimpleNamespace(
            call_sid=sid, turn_count=3, stage=SimpleNamespace(value="discover")
        )
        service = CostService(factory)
        recorder = CallCostRecorder(factory, service, state)
        try:
            async with factory() as session, session.begin():
                session.add(
                    Call(
                        id=call_id,
                        provider_call_id=sid,
                        direction="inbound",
                        status="in_progress",
                        started_at=datetime.now(UTC),
                        retention_until=retention,
                    )
                )
            turn = recorder.capture_turn()
            state.turn_count = 8
            state.stage.value = "close"
            await asyncio.gather(
                recorder.llm(None, str(uuid4()), turn=turn),
                recorder.speech("stt", str(uuid4()), turn=turn, audio_seconds=1),
                recorder.speech("tts", str(uuid4()), turn=turn, characters=5),
            )
            expected = uuid5(NAMESPACE_URL, f"roma:{call_id}:turn:3")
            report = await service.summary(call_id=call_id, group_by="turn")
            assert report["usage_events"] == 3
            assert len(report["groups"]) == 1
            assert report["groups"][0]["turn"] == str(expected)
            assert report["groups"][0]["usage_events"] == 3
            async with factory() as session:
                rows = (
                    await session.scalars(select(CallTurn).where(CallTurn.call_id == call_id))
                ).all()
                assert len(rows) == 1
                assert rows[0].turn_number == 3
                assert rows[0].conversation_stage == "discover"
                assert rows[0].transcript is None
                assert rows[0].retention_until == retention
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.postgres
def test_call_recorder_reuses_existing_turn_without_overwriting_its_metadata(cost_database):
    from types import SimpleNamespace

    from roma.services.call_costs import CallCostRecorder

    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        call_id, turn_id, sid = uuid4(), uuid4(), str(uuid4())
        state = SimpleNamespace(
            call_sid=sid, turn_count=2, stage=SimpleNamespace(value="discover")
        )
        retention = datetime(2034, 1, 1, tzinfo=UTC)
        service = CostService(factory)
        recorder = CallCostRecorder(factory, service, state)
        try:
            async with factory() as session, session.begin():
                session.add(
                    Call(
                        id=call_id,
                        provider_call_id=sid,
                        direction="inbound",
                        status="in_progress",
                        started_at=datetime.now(UTC),
                    )
                )
                await session.flush()
                session.add(
                    CallTurn(
                        id=turn_id,
                        call_id=call_id,
                        turn_number=2,
                        speaker="agent",
                        route="safety",
                        conversation_stage="open",
                        retention_until=retention,
                    )
                )
            await recorder.speech(
                "stt", str(uuid4()), turn=recorder.capture_turn(), audio_seconds=1
            )
            report = await service.summary(call_id=call_id, group_by="turn")
            assert report["groups"][0]["turn"] == str(turn_id)
            async with factory() as session:
                row = await session.get(CallTurn, turn_id)
                assert row.conversation_stage == "open"
                assert row.route == "safety"
                assert row.retention_until == retention
                assert row.transcript is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_call_recorder_accounting_failure_preserves_inflight_cancellation():
    from types import SimpleNamespace

    from roma.services.call_costs import CallCostRecorder

    async def run():
        recorder = CallCostRecorder(None, None, SimpleNamespace())
        entered = asyncio.Event()

        async def unavailable(turn):
            raise RuntimeError("synthetic accounting outage")

        recorder._identity = unavailable

        async def interrupted():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await recorder.speech("stt", "cancelled-request", audio_seconds=1)

        task = asyncio.create_task(interrupted())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
