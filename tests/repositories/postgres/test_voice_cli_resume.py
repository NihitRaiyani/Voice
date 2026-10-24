"""CLI pause/resume uses disposable SQL and scripted ownership, never speech models."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from roma.core.voice_errors import VoiceFailure
from roma.domain.conversation.state import CallState
from roma.repositories.postgres.conversation_state import PostgresConversationStateStore
from roma.repositories.postgres.models import Call, CallEvent, PrivacyTombstone
from roma.repositories.postgres.voice_lab import PostgresVoiceLabStore
from roma.services.live_call_state import CheckpointMilestone, LiveCallStateStore
from sqlalchemy import select
from tests.repositories.postgres.test_benchmarks import (
    benchmark_database,
    disposable_postgres_url,
)
from tests.services.test_voice_prototype_cli import cli

__all__ = ["benchmark_database", "disposable_postgres_url"]
pytestmark = pytest.mark.postgres


def test_pause_new_owner_restores_sql_identity_attempts_and_terminal_protection(
    benchmark_database,
):
    async def run():
        module = cli()
        sid = "voice-lab-" + uuid4().hex
        owners = {}

        class Lease:
            def __init__(self, identity):
                self.sid, self.token = identity, uuid4()

            async def owned(self):
                return owners.get(self.sid) == self.token

            async def acquire(self):
                if self.sid in owners:
                    return False
                owners[self.sid] = self.token
                return True

            async def release(self):
                if await self.owned():
                    owners.pop(self.sid)
                    return True
                return False

        class Hot:
            async def save(self, state):
                pass

            async def load(self, identity):
                return None

        def live():
            return LiveCallStateStore(
                Hot(), PostgresConversationStateStore(benchmark_database), lease_factory=Lease
            )

        writer, reader = live(), live()
        store = PostgresVoiceLabStore(benchmark_database)
        state = CallState(
            call_sid=sid, stage="discover", turn_count=3, lead_name="Synthetic learner"
        )
        try:
            await store.start(sid)
            await writer.checkpoint(state, CheckpointMilestone.TURN_COMPLETED)
            await store.save(
                sid,
                {
                    "attempt": 4,
                    "turn_number": 3,
                    "status": "played",
                    "stage": "discover",
                    "language": "ENGLISH",
                    "caller": None,
                    "agent": None,
                    "timings": {},
                    "error_code": None,
                },
            )
            # A concurrent owner may not resume or reset the same call.
            with pytest.raises(VoiceFailure):
                await module.resume_lab_call(benchmark_database, reader, sid)
            await module.pause_lab_call(benchmark_database, writer, state, 4)
            async with benchmark_database() as session:
                call = await session.scalar(select(Call).where(Call.provider_call_id == sid))
                started_at = call.started_at
                assert call.status == "in_progress" and call.ended_at is None
                assert (
                    await session.scalar(
                        select(CallEvent.event_type).where(
                            CallEvent.call_id == call.id,
                            CallEvent.event_type == "voice_lab.paused",
                        )
                    )
                    == "voice_lab.paused"
                )
            await writer.aclose()
            restored, original_start, attempts = await module.resume_lab_call(
                benchmark_database, reader, sid
            )
            assert restored.to_dict() == state.to_dict()
            assert original_start == started_at and original_start <= datetime.now(UTC)
            assert attempts == 4
            await store.finish(sid, disconnected=True)
            await reader.aclose()
            terminal = live()
            try:
                with pytest.raises(VoiceFailure):
                    await module.resume_lab_call(benchmark_database, terminal, sid)
                async with benchmark_database() as session:
                    call = await session.scalar(
                        select(Call).where(Call.provider_call_id == sid)
                    )
                    assert call.status == "failed" and call.ended_at is not None
            finally:
                await terminal.aclose()
        finally:
            await writer.aclose()
            await reader.aclose()

    asyncio.run(run())


def test_restored_old_active_call_with_sql_tombstone_never_repopulates_hot_state(
    benchmark_database,
):
    async def run():
        module = cli()
        sid = "voice-lab-" + uuid4().hex
        store = PostgresVoiceLabStore(benchmark_database)
        await store.start(sid)
        await PostgresConversationStateStore(benchmark_database).save(
            CallState(call_sid=sid, stage="discover", turn_count=3)
        )
        async with benchmark_database() as session, session.begin():
            call = await session.scalar(select(Call).where(Call.provider_call_id == sid))
            # Model a restored pre-delete Call/checkpoint and newer durable privacy ledger.
            session.add(
                PrivacyTombstone(
                    provider_call_id=sid,
                    call_id=call.id,
                    action="delete",
                    cleanup_pending=False,
                )
            )
            assert call.status == "in_progress" and call.deleted_at is None

        class NoCachePublication:
            async def load(self, identity):
                pytest.fail("Tombstone must be checked before Redis/checkpoint restore")

        with pytest.raises(VoiceFailure):
            await module.resume_lab_call(benchmark_database, NoCachePublication(), sid)

    asyncio.run(run())
