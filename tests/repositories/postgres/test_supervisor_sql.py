import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from roma.domain.conversation.language import route_language
from roma.domain.conversation.state import CallState
from roma.repositories.postgres.models import Call, CallEvent, CallTurn, ConversationState
from roma.repositories.postgres.supervisor import SupervisorRepository
from roma.services.supervisor import SupervisorHub, TurnObservation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.repositories.postgres import test_costs

cost_database = test_costs.cost_database


@pytest.mark.postgres
def test_supervisor_sql_only_metadata_idempotent_milestones_and_measured_latency(cost_database):
    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        key = uuid4()
        sid = "synthetic-supervisor-" + str(key)
        now = datetime.now(UTC)
        try:
            async with factory() as session, session.begin():
                session.add(
                    Call(
                        id=key,
                        provider_call_id=sid,
                        direction="inbound",
                        status="in_progress",
                        language=None,
                        started_at=now - timedelta(seconds=3),
                    )
                )
                await session.flush()
                session.add(
                    ConversationState(
                        call_id=key,
                        policy_version="test",
                        conversation_stage="discover",
                        state={"turn_count": 2, "lead_name": "Private synthetic name"},
                        retention_until=now + timedelta(minutes=1),
                    )
                )
            hub = SupervisorHub(factory, None)
            state = CallState(
                call_sid=sid,
                stage="discover",
                inquiry_confirmed=True,
                lead_name="Private synthetic name",
            )
            route_language(state.language_router, "ગુજરાતી ભાષામાં જાણકારી જોઈએ")
            hub.on_state(state)
            observation = hub.queue.get_nowait()
            async with factory() as session, session.begin():
                repository = SupervisorRepository(session)
                assert await repository.observe(observation) == key
                await repository.observe(observation)
                await repository.observe_turn(TurnObservation(sid, 2, "discover", 123))
                await repository.observe_turn(TurnObservation(sid, 2, "discover", 123))
            async with factory() as session:
                values = await SupervisorRepository(session).snapshot(call_id=key)
                assert len(values) == 1
                value = values[0]
                assert value.turn == 2 and value.language == "gu"
                assert value.latency_p95_ms == 123 and not value.booking_confirmed
                assert "Private synthetic name" not in value.model_dump_json()
                assert sid not in value.model_dump_json()
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(CallEvent)
                        .where(CallEvent.call_id == key)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(CallTurn)
                        .where(CallTurn.call_id == key)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(run())
