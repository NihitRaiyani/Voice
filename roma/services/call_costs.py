"""Call-scoped normalized usage with admission-time, metadata-only turn identity."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from roma.domain.costs.usage import UsageEvent
from roma.repositories.postgres.models.calls import Call, CallTurn


@dataclass(frozen=True, slots=True)
class CostTurn:
    turn_number: int
    stage: str

    def __post_init__(self):
        if type(self.turn_number) is not int or self.turn_number < 1:
            raise ValueError("Usage requires a positive turn number")
        if self.stage not in {
            "open",
            "discover",
            "value",
            "structure",
            "pivot",
            "objection",
            "close",
        }:
            raise ValueError("Usage requires a valid conversation stage")


class CallCostRecorder:
    def __init__(self, session_factory, costs, state):
        self.session_factory, self.costs, self.state = session_factory, costs, state
        self.call_id = None

    def capture_turn(self, turn_number: int | None = None) -> CostTurn:
        """Snapshot before inference/queue waits; STT supplies its upcoming ordinal."""
        return CostTurn(
            max(1, self.state.turn_count) if turn_number is None else turn_number,
            self.state.stage.value,
        )

    @asynccontextmanager
    async def _accounting(self):
        try:
            async with asyncio.timeout(2):
                yield
        except Exception:
            # Usage writes in a finally block must preserve the interrupted turn's
            # cancellation even if SQL fails during that final accounting attempt.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError from None
            raise

    async def _call_id(self):
        async with self.session_factory() as session:
            self.call_id = await session.scalar(
                select(Call.id).where(
                    Call.provider_call_id == self.state.call_sid,
                    Call.deleted_at.is_(None),
                )
            )
        if self.call_id is None:
            raise ValueError("Usage requires durable call identity")
        return self.call_id

    async def _identity(self, turn: CostTurn | None):
        if turn is None:
            return await self._call_id(), None  # Legacy sinks remain uncorrelated.
        if not isinstance(turn, CostTurn):
            raise ValueError("Usage requires a captured turn context")
        async with self.session_factory() as session, session.begin():
            call = await session.scalar(
                select(Call).where(
                    Call.provider_call_id == self.state.call_sid, Call.deleted_at.is_(None)
                )
            )
            if call is None:
                raise ValueError("Usage requires durable call identity")
            call_id = self.call_id = call.id
            proposed = uuid5(NAMESPACE_URL, f"roma:{call_id}:turn:{turn.turn_number}")
            await session.execute(
                insert(CallTurn)
                .values(
                    id=proposed,
                    call_id=call_id,
                    turn_number=turn.turn_number,
                    speaker="agent",
                    conversation_stage=turn.stage,
                    route="cost",
                    retention_until=call.retention_until,
                )
                .on_conflict_do_nothing(index_elements=[CallTurn.call_id, CallTurn.turn_number])
            )
            turn_id = await session.scalar(
                select(CallTurn.id).where(
                    CallTurn.call_id == call_id, CallTurn.turn_number == turn.turn_number
                )
            )
        return call_id, turn_id

    async def llm(self, final, request_id, *, turn: CostTurn | None = None):
        async with self._accounting():
            call_id, turn_id = await self._identity(turn)
            return await self.costs.record_llm(
                final, request_id, call_id=call_id, turn_id=turn_id
            )

    async def speech(self, service, request_id, *, turn: CostTurn | None = None, **values):
        async with self._accounting():
            call_id, turn_id = await self._identity(turn)
            return await self.costs.record_speech(
                service,
                request_id,
                call_id=call_id,
                turn_id=turn_id,
                provider="local_http",
                **values,
            )

    async def finish(self, started_at, ended_at=None):
        ended_at = ended_at or datetime.now(UTC)
        async with self._accounting():
            call_id = await self._call_id()
            return await self.costs.record(
                UsageEvent(
                    event_key=f"carrier:{call_id}:final",
                    provider="twilio",
                    service="telephony",
                    call_id=call_id,
                    occurred_at=ended_at,
                    carrier_seconds=Decimal(
                        str(max(0, (ended_at - started_at).total_seconds()))
                    ),
                )
            )
