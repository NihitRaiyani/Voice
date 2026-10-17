import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from roma.domain.persistence import RecordNotFound
from roma.domain.safety.audit import SafetyContext, check_sentence
from roma.repositories.postgres.models import Call, CallTurn, SafetyEvent
from roma.repositories.postgres.safety import PostgresSafetyEventStore
from roma.schemas.rest_api import SafetyEventsQuery, SafetyStatsQuery
from roma.services.rest_api_service import RestApiService, RestApiServiceError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.repositories.postgres.test_migrations import _local_postgres_url

pytestmark = pytest.mark.postgres


@pytest.fixture()
def session_factory():
    # Explicit native PostgreSQL: this section does not download/start Docker images.
    with _local_postgres_url() as url:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            yield factory
        finally:
            asyncio.run(engine.dispose())


async def create_call(factory, sid):
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        call = Call(
            id=uuid4(),
            provider_call_id=sid,
            direction="inbound",
            status="in_progress",
            started_at=now,
            retention_until=now + timedelta(days=1),
        )
        session.add(call)
    return call.id


def test_audit_roundtrip_replay_call_scope_stats_and_soft_delete(session_factory):
    async def run():
        first_id = await create_call(session_factory, "synthetic-first")
        second_id = await create_call(session_factory, "synthetic-second")
        store = PostgresSafetyEventStore(session_factory)
        _, _, events = check_sentence("Fees 25000 and package 5 lakh hai.")
        context = SafetyContext("synthetic-first", 2, "value")
        await store.record(context, events)
        await store.record(context, events)  # exact retry is idempotent
        _, _, other = check_sentence("Special discount hai.")
        await store.record(SafetyContext("synthetic-second", 1, "value"), other)
        service = RestApiService(session_factory)
        rows, meta = await service.list_call_safety_events(first_id, SafetyEventsQuery(limit=1))
        assert meta["total"] == 2 and len(rows) == 1
        assert meta["filters"] == {"call_id": str(first_id)}
        assert rows[0]["call_id"] == str(first_id) and rows[0]["turn_id"]
        assert rows[0]["latency_ms"] >= 0 and rows[0]["replacement_used"]
        assert rows[0]["detected_at"] and rows[0]["rule_id"].startswith("speech.")
        serialized = str(rows)
        for private in ("25000", "synthetic-first", "5 lakh"):
            assert private not in serialized
        totals = await service.safety_stats(SafetyStatsQuery(call_id=first_id))
        assert totals["total"] == 2 and totals["by_category"] == {"FEE": 1, "SALARY": 1}
        filtered, meta = await service.list_safety_events(
            SafetyEventsQuery(category="DISCOUNT")
        )
        assert len(filtered) == meta["total"] == 1 and filtered[0]["call_id"] == str(second_id)
        async with session_factory() as session, session.begin():
            turns = list(
                await session.scalars(select(CallTurn).where(CallTurn.call_id == first_id))
            )
            assert len(turns) == 1 and turns[0].turn_number == 2 and turns[0].transcript is None
            row = await session.get(SafetyEvent, events[0].id)
            assert row.retention_until == turns[0].retention_until
            row.deleted_at = datetime.now(UTC)
        assert (await service.safety_stats(SafetyStatsQuery(call_id=first_id)))["total"] == 1
        empty = await service.safety_stats(SafetyStatsQuery(rule="absent"))
        assert empty == {"total": 0, "by_category": {}, "by_rule": {}}
        with pytest.raises(RestApiServiceError) as error:
            await service.list_call_safety_events(uuid4(), SafetyEventsQuery())
        assert error.value.status_code == 404

    asyncio.run(run())


def test_audit_missing_call_leaves_no_orphan_turn_or_event(session_factory):
    async def run():
        _, _, events = check_sentence("Fees 25000 hai.")
        with pytest.raises(RecordNotFound):
            await PostgresSafetyEventStore(session_factory).record(
                SafetyContext("missing", 1, "value"), events
            )
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(CallTurn)) == 0
            assert await session.scalar(select(func.count()).select_from(SafetyEvent)) == 0

    asyncio.run(run())


def test_audit_reuses_existing_turn_without_overwriting_transcript(session_factory):
    async def run():
        call_id = await create_call(session_factory, "synthetic-existing")
        turn_id = uuid4()
        async with session_factory() as session, session.begin():
            session.add(
                CallTurn(
                    id=turn_id,
                    call_id=call_id,
                    turn_number=1,
                    speaker="agent",
                    transcript="Approved original text",
                )
            )
        _, _, events = check_sentence("Fees 25000 hai.")
        await PostgresSafetyEventStore(session_factory).record(
            SafetyContext("synthetic-existing", 1, "value"), events
        )
        async with session_factory() as session:
            row = await session.get(SafetyEvent, events[0].id)
            turn = await session.get(CallTurn, turn_id)
            assert row.turn_id == turn_id and turn.transcript == "Approved original text"

    asyncio.run(run())
