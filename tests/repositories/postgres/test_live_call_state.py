"""Real checkpoint recovery using only disposable PostgreSQL and Redis servers."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import CallState
from roma.repositories.postgres.conversation_state import PostgresConversationStateStore
from roma.repositories.postgres.models import Call
from roma.repositories.redis.call_lease import RedisCallLease
from roma.repositories.redis.conversation_state import RedisCallStateStore, state_key
from roma.services.live_call_state import CheckpointMilestone, LiveCallStateStore
from tests.repositories.postgres.test_benchmarks import (
    benchmark_database,
    disposable_postgres_url,
)
from tests.workers.test_dramatiq_worker import redis_url

__all__ = ["benchmark_database", "disposable_postgres_url", "redis_url"]
pytestmark = pytest.mark.postgres


async def _persist_call(factory, sid):
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        session.add(
            Call(
                id=uuid4(),
                provider_call_id=sid,
                direction="inbound",
                status="in_progress",
                started_at=now,
                language="hi",
                retention_until=now + timedelta(hours=4),
            )
        )


def _live_store(factory, redis_url, lease_client):
    return LiveCallStateStore(
        RedisCallStateStore(client=Redis.from_url(redis_url)),
        PostgresConversationStateStore(factory),
        lease_factory=lambda sid: RedisCallLease(lease_client, sid),
    )


def _accepted_state(sid):
    return CallState(
        call_sid=sid,
        stage=ConversationStage.CLOSE,
        turn_count=8,
        stage_turn_count=1,
        lead_name="Synthetic learner",
        current_status="student",
        education="BCom",
        passing_year="2026",
        city="Vadodara",
        slot_attempts={"lead_name": 1, "city": 1},
        slots_offered=["2026-10-26T11:00:00+05:30", "2026-10-26T17:00:00+05:30"],
        accepted_slot="2026-10-26T11:00:00+05:30",
        slot_status="accepted",
        booking_required=True,
        readback_confirmed=True,
    )


@pytest.mark.parametrize("hot_loss", ["missing", "expired"])
def test_new_owner_restores_sql_stage_and_slots_after_hot_state_loss(
    benchmark_database, redis_url, hot_loss
):
    async def run():
        sid = "voice-lab-recovery-" + uuid4().hex
        await _persist_call(benchmark_database, sid)
        client = Redis.from_url(redis_url)
        writer = _live_store(benchmark_database, redis_url, client)
        restored_store = _live_store(benchmark_database, redis_url, client)
        expected = _accepted_state(sid)
        try:
            await writer.checkpoint(expected, CheckpointMilestone.STAGE_CHANGED)
            durable = PostgresConversationStateStore(benchmark_database)
            assert (await durable.load(sid)).to_dict() == expected.to_dict()
            await writer.aclose()
            key = state_key(sid)
            assert await client.exists(key)
            if hot_loss == "expired":
                assert await client.pexpire(key, 1)
                await asyncio.sleep(0.02)
            else:
                assert await client.delete(key) == 1
            assert await RedisCallStateStore(client=client).load(sid) is None

            restored = await restored_store.load(sid)
            assert restored.to_dict() == expected.to_dict()
            assert restored.stage is ConversationStage.CLOSE
            assert restored.accepted_slot == expected.accepted_slot
            assert restored.next_discovery_slot() is None
            assert restored_store.metrics()["recoveries"] == 1
            hot = await RedisCallStateStore(client=client).load(sid)
            assert hot.to_dict() == expected.to_dict()
            assert 0 < await client.ttl(key) <= 3600
        finally:
            await writer.aclose()
            await restored_store.aclose()
            await client.aclose()

    asyncio.run(run())


def test_expired_owner_cannot_overwrite_or_release_recovered_successor(
    benchmark_database, redis_url
):
    async def run():
        sid = "voice-lab-successor-" + uuid4().hex
        await _persist_call(benchmark_database, sid)
        client = Redis.from_url(redis_url)
        previous = _live_store(benchmark_database, redis_url, client)
        successor = _live_store(benchmark_database, redis_url, client)
        accepted = _accepted_state(sid)
        durable = PostgresConversationStateStore(benchmark_database)
        try:
            await previous.checkpoint(accepted, CheckpointMilestone.TURN_COMPLETED)
            (lease_key,) = await client.keys("roma:call-lease:*")
            assert await client.pexpire(lease_key, 1)
            await asyncio.sleep(0.02)
            assert not await previous.lease.owned()
            assert await client.delete(state_key(sid)) == 1

            restored = await successor.load(sid)
            assert restored.to_dict() == accepted.to_dict()
            assert successor.metrics()["recoveries"] == 1
            restored.stage = ConversationStage.OBJECTION
            restored.turn_count += 1
            restored.objection_counts["timing"] = 1
            await successor.checkpoint(restored, CheckpointMilestone.TURN_COMPLETED)

            stale = CallState(call_sid=sid, stage=ConversationStage.OPEN, city="Stale city")
            with pytest.raises(VoiceFailure) as rejected:
                await previous.checkpoint(stale, CheckpointMilestone.STATE_CHANGED)
            assert rejected.value.code is VoiceErrorCode.DB
            with pytest.raises(VoiceFailure):
                await previous.load(sid)
            assert (await durable.load(sid)).to_dict() == restored.to_dict()
            assert not await previous.lease.release()
            await previous.aclose()
            assert await successor.lease.owned()
            assert (await successor.load(sid)).to_dict() == restored.to_dict()
            hot = await RedisCallStateStore(client=client).load(sid)
            assert hot.to_dict() == restored.to_dict()
        finally:
            await previous.aclose()
            await successor.aclose()
            await client.aclose()

    asyncio.run(run())
