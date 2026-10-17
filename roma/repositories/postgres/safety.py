"""Short, idempotent safety audit writes using the existing schema."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from roma.domain.persistence import RecordNotFound
from roma.domain.safety.audit import SafetyActivation, SafetyContext
from roma.repositories.postgres.models import Call, CallTurn, SafetyEvent


class PostgresSafetyEventStore:
    def __init__(self, session_factory, *, source: str = "text") -> None:
        if source not in {"text", "voice"}:
            raise ValueError("Unknown safety source")
        self._session_factory = session_factory
        self.source = source

    async def record(
        self, context: SafetyContext, events: tuple[SafetyActivation, ...]
    ) -> None:
        if not events:
            return
        if context.turn_number < 1 or context.stage not in {
            "open",
            "discover",
            "value",
            "structure",
            "pivot",
            "objection",
            "close",
        }:
            raise ValueError("Invalid safety turn context")
        async with self._session_factory() as session, session.begin():
            call = await session.scalar(
                select(Call).where(
                    Call.provider_call_id == context.call_sid, Call.deleted_at.is_(None)
                )
            )
            if call is None:
                raise RecordNotFound("Safety audit requires an existing call")
            # No transcript is needed for audit correlation. Reuse a pre-existing turn.
            proposed = uuid5(NAMESPACE_URL, f"roma:{call.id}:turn:{context.turn_number}")
            await session.execute(
                insert(CallTurn)
                .values(
                    id=proposed,
                    call_id=call.id,
                    turn_number=context.turn_number,
                    speaker="agent",
                    conversation_stage=context.stage,
                    route="safety",
                    created_at=events[0].detected_at,
                    retention_until=call.retention_until,
                )
                .on_conflict_do_nothing(index_elements=[CallTurn.call_id, CallTurn.turn_number])
            )
            turn_id = await session.scalar(
                select(CallTurn.id).where(
                    CallTurn.call_id == call.id, CallTurn.turn_number == context.turn_number
                )
            )
            for event in events:
                await session.execute(
                    insert(SafetyEvent)
                    .values(
                        id=event.id,
                        call_id=call.id,
                        turn_id=turn_id,
                        rule=event.rule_id,
                        original_category=event.category,
                        replacement_type="approved_line",
                        created_at=event.detected_at,
                        retention_until=call.retention_until,
                        metadata_={
                            "policy_version": event.policy_version,
                            "replacement_used": event.replacement_used,
                            "latency_ms": event.latency_ms,
                            "turn_number": context.turn_number,
                            "stage": context.stage,
                            "source": self.source,
                        },
                    )
                    .on_conflict_do_nothing(index_elements=[SafetyEvent.id])
                )
