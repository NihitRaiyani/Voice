"""SQL supervisor projections and idempotent prospective funnel milestones."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Numeric, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert

from roma.repositories.postgres.models import (
    Appointment,
    Call,
    CallCost,
    CallEvent,
    CallTurn,
    ConversationState,
    SafetyEvent,
)
from roma.schemas.supervisor import SupervisorEvent


class SupervisorRepository:
    def __init__(self, session):
        self.session = session

    async def observe(self, value):
        call_id = await self.session.scalar(
            select(Call.id).where(Call.provider_call_id == value.sid, Call.deleted_at.is_(None))
        )
        if call_id is None:
            return None
        await self.session.execute(
            update(Call).where(Call.id == call_id).values(language=value.language)
        )
        # These are prospective controller proofs, never inferred from final_stage.
        for name, proved in (
            ("greeting_completed", value.greeting),
            ("discovery_completed", value.discovery),
            ("value_presented", value.value),
            ("booking_offered", value.offered),
            ("slot_accepted", value.accepted),
        ):
            if proved:
                await self.session.execute(
                    insert(CallEvent)
                    .values(
                        call_id=call_id,
                        event_type="analytics." + name,
                        conversation_stage=value.stage,
                        payload={"source": "durable_controller"},
                        occurred_at=value.at,
                        idempotency_key=f"analytics:{call_id}:{name}",
                    )
                    .on_conflict_do_nothing(index_elements=[CallEvent.idempotency_key])
                )
        return call_id

    async def observe_turn(self, value):
        call = await self.session.scalar(
            select(Call).where(Call.provider_call_id == value.sid, Call.deleted_at.is_(None))
        )
        if call is None:
            return None
        await self.session.execute(
            insert(CallTurn)
            .values(
                id=uuid5(NAMESPACE_URL, f"roma:{call.id}:turn:{value.turn}"),
                call_id=call.id,
                turn_number=value.turn,
                speaker="agent",
                conversation_stage=value.stage,
                route="telemetry",
                latency_ms=value.latency_ms,
                retention_until=call.retention_until,
            )
            .on_conflict_do_update(
                index_elements=[CallTurn.call_id, CallTurn.turn_number],
                set_={"latency_ms": value.latency_ms},
            )
        )
        return call.id

    async def snapshot(self, *, call_id=None, limit=100):
        if not 1 <= limit <= 100:
            raise ValueError("Snapshot limit must be 1..100")
        stage = func.coalesce(ConversationState.conversation_stage, Call.final_stage, "unknown")
        query = (
            select(
                Call.id,
                Call.started_at,
                Call.ended_at,
                Call.language,
                stage,
                ConversationState.state["turn_count"].as_integer(),
            )
            .outerjoin(ConversationState, ConversationState.call_id == Call.id)
            .where(Call.deleted_at.is_(None))
        )
        query = (
            query.where(Call.id == call_id)
            if call_id
            else query.where(Call.status.in_(("pending", "ringing", "in_progress")))
        )
        rows = (
            await self.session.execute(query.order_by(Call.started_at.desc()).limit(limit))
        ).all()
        ids = [row[0] for row in rows]
        if not ids:
            return []
        safety = dict(
            (
                await self.session.execute(
                    select(SafetyEvent.call_id, func.count())
                    .where(SafetyEvent.call_id.in_(ids))
                    .group_by(SafetyEvent.call_id)
                )
            ).all()
        )
        latency = dict(
            (
                await self.session.execute(
                    select(
                        CallTurn.call_id,
                        func.percentile_cont(0.95).within_group(CallTurn.latency_ms),
                    )
                    .where(CallTurn.call_id.in_(ids))
                    .group_by(CallTurn.call_id)
                )
            ).all()
        )
        booked = set(
            (
                await self.session.scalars(
                    select(Appointment.call_id).where(
                        Appointment.call_id.in_(ids),
                        Appointment.status.in_(("booked", "confirmed", "completed")),
                    )
                )
            ).all()
        )
        costs = {key: [] for key in ids}
        for key, currency, amount in (
            await self.session.execute(
                select(
                    CallCost.call_id,
                    CallCost.currency,
                    func.sum(cast(CallCost.metadata_["exact_amount"].astext, Numeric)),
                )
                .where(
                    CallCost.call_id.in_(ids), CallCost.metadata_["cost_engine"].astext == "v1"
                )
                .group_by(CallCost.call_id, CallCost.currency)
            )
        ).all():
            costs[key].append({"currency": currency, "amount": amount})
        now = datetime.now(UTC)
        return [
            SupervisorEvent(
                call_id=key,
                observed_at=now,
                stage=current_stage,
                language=language if language in {"gu", "hi", "en", "code_mix"} else "unknown",
                turn=turn or 0,
                duration_seconds=min(86400, max(0, ((ended or now) - started).total_seconds())),
                booking_confirmed=key in booked,
                safety_hits=safety.get(key, 0),
                latency_p95_ms=latency.get(key),
                costs=costs[key],
            )
            for key, started, ended, language, current_stage, turn in rows
        ]
