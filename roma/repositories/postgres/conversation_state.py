"""PostgreSQL-backed latest conversation checkpoint store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from roma.domain.conversation.state import CallState
from roma.domain.persistence import ConversationStateRecord, RecordNotFound
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork

SessionFactory = Callable[[], AsyncSession]
CONVERSATION_STATE_SCHEMA_VERSION = 1
DEFAULT_CONVERSATION_POLICY_VERSION = "roma-v4-hinglish-1"


class PostgresConversationStateStore:
    """Saves and restores call state using short PostgreSQL units of work.

    Redis remains the live low-latency cache. This adapter is the durable checkpoint path
    for resumed calls that already have a persisted ``calls.provider_call_id``.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        policy_version: str = DEFAULT_CONVERSATION_POLICY_VERSION,
        retention_ttl: timedelta = timedelta(hours=4),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._policy_version = policy_version
        self._retention_ttl = retention_ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    async def load(self, call_sid: str) -> CallState | None:
        now = self._clock()
        async with PostgresUnitOfWork(self._session_factory) as uow:
            record = await uow.conversation_states.get_by_provider_call_id(call_sid)
        if record is None:
            return None
        if record.schema_version != CONVERSATION_STATE_SCHEMA_VERSION:
            return None
        if record.policy_version != self._policy_version:
            return None
        if record.retention_until <= now:
            return None
        return CallState.from_dict(dict(record.state))

    async def save(self, state: CallState) -> None:
        if not state.call_sid:
            raise ValueError("durable conversation checkpoint requires call_sid")
        now = self._clock()
        async with PostgresUnitOfWork(self._session_factory) as uow:
            call = await uow.calls.get_by_provider_call_id(state.call_sid)
            if call is None:
                raise RecordNotFound("call does not exist for checkpoint")
            await uow.conversation_states.save_latest(
                ConversationStateRecord(
                    id=uuid4(),
                    call_id=call.id,
                    schema_version=CONVERSATION_STATE_SCHEMA_VERSION,
                    revision=1,
                    policy_version=self._policy_version,
                    conversation_stage=state.stage.value,
                    state=state.to_dict(),
                    retention_until=now + self._retention_ttl,
                    created_at=now,
                    updated_at=now,
                )
            )
            await uow.commit()


__all__ = [
    "CONVERSATION_STATE_SCHEMA_VERSION",
    "DEFAULT_CONVERSATION_POLICY_VERSION",
    "PostgresConversationStateStore",
]
