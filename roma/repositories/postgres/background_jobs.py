"""Transactional post-call outbox and leased PostgreSQL job consumer.

The existing ``followup_jobs`` table is used for post-call work as well as scheduled
follow-ups. Its unique idempotency key and status/lease columns are the job contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roma.domain.persistence import CallRecord, FollowupJobRecord

from .models import Call, Caller, CallEvent, FollowupJob

ACTIVE_JOB_TYPES = ("compute_statistics", "summarize_call", "update_lead")
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
            statement = statement.on_conflict_do_update(
                index_elements=[Call.provider_call_id],
                set_=updates,
            ).returning(Call.id)
            call_id = (await session.execute(statement)).scalar_one()
            for job in jobs:
                await session.execute(
                    insert(FollowupJob)
                    .values(
                        id=job.id,
                        call_id=call_id,
                        job_type=job.job_type,
                        payload=dict(job.payload),
                        status=job.status,
                        attempts=0,
                        available_at=job.available_at,
                        idempotency_key=job.idempotency_key,
                        created_at=job.created_at,
                        updated_at=job.updated_at,
                    )
                    .on_conflict_do_nothing(index_elements=[FollowupJob.idempotency_key])
                )
        return call_id

    async def claim(self, *, now: datetime, owner: str) -> ClaimedJob | None:
        """Claim one due job; reclaim a crashed worker's expired lease."""
        stale_before = now - timedelta(seconds=LEASE_SECS)
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(FollowupJob)
                .where(
                    FollowupJob.job_type.in_(ACTIVE_JOB_TYPES),
                    FollowupJob.call_id.is_not(None),
                    or_(
                        and_(
                            FollowupJob.status.in_(("pending", "failed")),
                            FollowupJob.available_at <= now,
                        ),
                        and_(
                            FollowupJob.status == "running",
                            FollowupJob.locked_at <= stale_before,
                        ),
                    ),
                )
                .order_by(
                    FollowupJob.available_at,
                    FollowupJob.created_at,
                    FollowupJob.job_type,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
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

    async def fail(self, job: ClaimedJob, *, owner: str, now: datetime, error_type: str) -> str:
        """Exponential retry with a capped delay; terminal failures remain queryable."""
        async with self._session_factory() as session, session.begin():
            row = await _owned_job(session, job.id, owner)
            if row is None:
                return "lost_lease"
            row.status = "dead_letter" if row.attempts >= MAX_ATTEMPTS else "failed"
            row.available_at = now + timedelta(seconds=min(300, 2**row.attempts))
            row.last_error = error_type[:128]
            row.lock_owner = None
            row.locked_at = None
            return row.status


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
