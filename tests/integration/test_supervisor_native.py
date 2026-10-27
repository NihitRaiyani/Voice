"""Real disposable SQL -> Redis Pub/Sub -> WebSocket adapter metadata flow."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from roma.domain.conversation.language import route_language
from roma.domain.conversation.state import CallState
from roma.repositories.postgres.models import Call
from roma.services.supervisor import SupervisorHub, relay_supervisor
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.repositories.postgres import test_costs
from tests.workers import test_dramatiq_worker

cost_database = test_costs.cost_database
redis_url = test_dramatiq_worker.redis_url


@pytest.mark.postgres
def test_native_sql_checkpoint_metadata_reaches_supervisor_over_redis(cost_database, redis_url):
    async def run():
        engine = create_async_engine(cost_database)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        redis = Redis.from_url(redis_url)
        key = uuid4()
        sid = "synthetic-live-supervisor-" + str(key)
        state = CallState(call_sid=sid, lead_name="Private synthetic content", stage="discover")
        route_language(state.language_router, "ગુજરાતી માહિતી જોઈએ")
        subscribed, delivered = asyncio.Event(), asyncio.Event()

        class Socket:
            async def send_json(self, value):
                if value["kind"] == "resync_required":
                    subscribed.set()
                else:
                    assert value["call_id"] == str(key)
                    assert value["language"] == "gu"
                    assert "Private synthetic content" not in str(value)
                    assert sid not in str(value)
                    delivered.set()

        async def authorize():
            return True

        hub = SupervisorHub(factory, redis)
        relay = None
        try:
            async with factory() as session, session.begin():
                session.add(
                    Call(
                        id=key,
                        provider_call_id=sid,
                        direction="inbound",
                        status="in_progress",
                        started_at=datetime.now(UTC),
                    )
                )
            relay = asyncio.create_task(relay_supervisor(Socket(), redis, authorize))
            await asyncio.wait_for(subscribed.wait(), 2)
            hub.start()
            hub.on_state(state)
            await asyncio.wait_for(delivered.wait(), 3)
        finally:
            if relay is not None:
                relay.cancel()
                await asyncio.gather(relay, return_exceptions=True)
            await hub.aclose()
            await redis.aclose()
            await engine.dispose()

    asyncio.run(run())
