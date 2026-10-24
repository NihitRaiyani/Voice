"""Transactional post-call outbox and leased PostgreSQL job consumer.

The existing ``followup_jobs`` table is used for post-call work as well as scheduled
follow-ups. Its unique idempotency key and status/lease columns are the job contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roma.domain.persistence import CallRecord, FollowupJobRecord

from .models import Call, Caller, CallEvent, FollowupJob

ACTIVE_JOB_TYPES = (
    "compute_statistics",
    "summarize_call",
    "update_lead",
    "send_followup",
    "process_recording",
)
LEASE_SECS = 300
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    call_id: UUID
    job_type: str
    payload: dict[str, Any]
    attempts: int
    idempotency_key: str


class BackgroundJobStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_call_and_jobs(
        self,
        call: CallRecord,
        jobs: tuple[FollowupJobRecord, ...],
        *,
        caller_digest: str | None = None,
    ) -> UUID:
        """One commit owns both the call outcome and all job intents.

        Replayed post-call messages reuse the provider call ID and each job's unique key.
        This closes the crash window between saving a call and queuing its work.
        """
        if not call.provider_call_id:
            raise ValueError("post-call work requires a provider call ID")
        async with self._session_factory() as session, session.begin():
            caller_id = call.caller_id
            if caller_digest is not None:
                caller_id = (
                    await session.execute(
                        insert(Caller)
                        .values(phone_hash=caller_digest)
                        .on_conflict_do_update(
                            index_elements=[Caller.phone_hash],
                            set_={"updated_at": call.updated_at},
                        )
                        .returning(Caller.id)
                    )
                ).scalar_one()
            updates = {
                "status": "completed",
                "started_at": call.started_at,
                "ended_at": call.ended_at,
                "final_stage": call.final_stage,
                "updated_at": call.updated_at,
            }
            if caller_id is not None:
                updates["caller_id"] = caller_id
            statement = insert(Call).values(
                id=call.id,
                provider_call_id=call.provider_call_id,
                caller_id=caller_id,
                direction=call.direction,
                status="completed",
                language=call.language,
                started_at=call.started_at,
                ended_at=call.ended_at,
                final_stage=call.final_stage,
                booking_status=call.booking_status,
                created_at=call.created_at,
                updated_at=call.updated_at,
            )
            returning = statement.on_conflict_do_update(
                index_elements=[Call.provider_call_id],
                set_=updates,
            ).returning(Call.id)
            call_id = (await session.execute(returning)).scalar_one()
            for job in jobs:
                await session.execute(
                    insert(FollowupJob)
                    .values(
                        id=job.id,
                        call_id=call_id,
                        job_type=job.job_type,
                        payload=dict(job.payload),
                        status=job.status,
                        attempts=job.attempts,
                        last_error=job.last_error,
                        available_at=job.available_at,
                        idempotency_key=job.idempotency_key,
                        created_at=job.created_at,
                        updated_at=job.updated_at,
                    )
                    .on_conflict_do_nothing(index_elements=[FollowupJob.idempotency_key])
                )
        return call_id

    async def dispatchable_ids(self, *, now: datetime, limit: int = 100) -> list[UUID]:
        """Read ready job IDs without claiming them or assuming Redis accepted a message.

        Republishing is safe. Only the consumer's database claim advances job attempts.
        Failed publishing or Redis data loss therefore leaves the durable work retryable.
        """
        return [job_id for job_id, _ in await self.dispatchable_jobs(now=now, limit=limit)]

    async def dispatchable_jobs(
        self, *, now: datetime, limit: int = 100
    ) -> list[tuple[UUID, str]]:
        """Return ID/type routing metadata only; scheduled jobs stay in PostgreSQL."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Dispatch limit must be an integer from 1 to 1000")
        async with self._session_factory() as session:
            rows = await session.execute(
                _eligible_jobs(now)
                .with_only_columns(FollowupJob.id, FollowupJob.job_type)
                .limit(limit)
            )
            return [(job_id, job_type) for job_id, job_type in rows]

    async def claim(
        self, *, now: datetime, owner: str, job_id: UUID | None = None
    ) -> ClaimedJob | None:
        """Claim one due job; reclaim a crashed worker's expired lease."""
        async with self._session_factory() as session, session.begin():
            statement = _eligible_jobs(now)
            if job_id is not None:
                statement = statement.where(FollowupJob.id == job_id)
            row = await session.scalar(statement.with_for_update(skip_locked=True).limit(1))
            if row is None:
                return None
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "dead_letter"
                row.last_error = "lease_retries_exhausted"
                row.lock_owner = None
                row.locked_at = None
                return None
            row.attempts += 1
            row.status = "running"
            row.lock_owner = owner
            row.locked_at = now
            await session.flush()
            return ClaimedJob(
                id=row.id,
                call_id=row.call_id,
                job_type=row.job_type,
                payload=dict(row.payload),
                attempts=row.attempts,
                idempotency_key=row.idempotency_key,
            )

    async def complete(
        self,
        job: ClaimedJob,
        *,
        owner: str,
        now: datetime,
        event_type: str,
        event_payload: dict[str, object],
        caller_status: str | None = None,
    ) -> bool:
        """Commit the business effect and acknowledgement in the same transaction."""
        async with self._session_factory() as session, session.begin():
            row = await _owned_job(session, job.id, owner)
            if row is None:
                return False
            call = await session.get(Call, job.call_id)
            if call is None:
                raise RuntimeError("job call record is missing")
            needs_identity = caller_status is not None and call.caller_id is None
            if caller_status is not None and call.caller_id is not None:
                caller = await session.get(Caller, call.caller_id)
                if caller is not None:
                    caller.current_status = caller_status
                else:
                    needs_identity = True
            if needs_identity:
                event_type = "lead_update_requires_identity"
                event_payload = {"reason": "caller_not_linked"}
            await session.execute(
                insert(CallEvent)
                .values(
                    call_id=job.call_id,
                    event_type=event_type,
                    conversation_stage=call.final_stage,
                    payload=event_payload,
                    occurred_at=now,
                    idempotency_key=job.idempotency_key,
                )
                .on_conflict_do_nothing(index_elements=[CallEvent.idempotency_key])
            )
            row.status = "dead_letter" if needs_identity else "succeeded"
            row.lock_owner = None
            row.locked_at = None
            row.last_error = "caller_not_linked" if needs_identity else None
            return True

    async def fail(
        self,
        job: ClaimedJob,
        *,
        owner: str,
        now: datetime,
        error_type: str,
        permanent: bool = False,
    ) -> str:
        """Exponential retry with a capped delay; terminal failures remain queryable."""
        async with self._session_factory() as session, session.begin():
            row = await _owned_job(session, job.id, owner)
            if row is None:
                return "lost_lease"
            row.status = (
                "dead_letter" if permanent or row.attempts >= MAX_ATTEMPTS else "failed"
            )
            row.available_at = now + timedelta(seconds=min(300, 2**row.attempts))
            row.last_error = error_type[:128]
            row.lock_owner = None
            row.locked_at = None
            return row.status


def _eligible_jobs(now: datetime):
    return (
        select(FollowupJob)
        .where(
            FollowupJob.job_type.in_(ACTIVE_JOB_TYPES),
            FollowupJob.call_id.is_not(None),
            FollowupJob.deleted_at.is_(None),
            or_(
                and_(
                    FollowupJob.status.in_(("pending", "failed")),
                    FollowupJob.available_at <= now,
                ),
                and_(
                    FollowupJob.status == "running",
                    FollowupJob.locked_at <= now - timedelta(seconds=LEASE_SECS),
                ),
            ),
        )
        .order_by(
            case((FollowupJob.job_type == "summarize_call", 1), else_=0),
            FollowupJob.available_at,
            FollowupJob.created_at,
            FollowupJob.job_type,
        )
    )


async def _owned_job(session: AsyncSession, job_id: UUID, owner: str) -> FollowupJob | None:
    return await session.scalar(
        select(FollowupJob)
        .where(
            FollowupJob.id == job_id,
            FollowupJob.status == "running",
            FollowupJob.lock_owner == owner,
        )
        .with_for_update()
    )
