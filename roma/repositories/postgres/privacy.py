"""Durable access auditing and retryable privacy disposal for managed call data."""

from hashlib import sha256
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from roma.repositories.postgres.models import (
    Appointment,
    AuditLog,
    Call,
    CallCost,
    Caller,
    CallEvent,
    CallTurn,
    ConversationState,
    FollowupJob,
    PrivacyTombstone,
    ProviderUsage,
    Recording,
    Role,
    SafetyEvent,
    User,
    UserRole,
)
from roma.services.recording_service import RecordingAccess, RecordingUnavailable


async def lock_privacy_identity(session, sid):
    # Same lock is taken by post-call producer before any caller/call upsert.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:sid, 31))"), {"sid": sid}
    )


class SQLPrivacyRepository:
    def __init__(self, session_factory):
        self.factory = session_factory

    async def is_deleted(self, sid):
        async with self.factory() as session:
            return bool(
                await session.scalar(
                    select(PrivacyTombstone.id).where(PrivacyTombstone.provider_call_id == sid)
                )
            )

    async def recording(self, recording_id):
        async with self.factory() as session:
            row = await session.execute(
                select(Recording, Call.deleted_at)
                .join(Call)
                .where(Recording.id == recording_id, Recording.storage_provider == "local")
            )
            pair = row.first()
            if pair is None:
                return None
            recording, deleted_at = pair
            return RecordingAccess(
                recording.id,
                recording.call_id,
                recording.object_key,
                recording.media_type,
                recording.retention_until,
                recording.consent_at,
                recording.status,
                bool(recording.deleted_at or deleted_at),
            )

    async def record_access(
        self, row, actor_id, action, *, now, request_id=None, correlation_id=None
    ):
        async with self.factory() as session, session.begin():
            # Call lock serializes audit admission with disposal. Recheck live role,
            # consent, expiry and deletion immediately before the durable audit commit.
            call = await session.scalar(
                select(Call).where(Call.id == row.call_id).with_for_update()
            )
            recording = await session.get(Recording, row.id, populate_existing=True)
            user = await session.get(User, actor_id, populate_existing=True)
            roles = set(
                await session.scalars(
                    select(Role.name).join(UserRole).where(UserRole.user_id == actor_id)
                )
            )
            if (
                user is None
                or not user.is_active
                or not roles.intersection({"admin", "counsellor"})
                or call is None
                or call.deleted_at is not None
                or recording is None
                or recording.status != "available"
                or recording.deleted_at is not None
                or recording.consent_at is None
                or not recording.disclosure_version
                or recording.retention_until <= now
            ):
                raise RecordingUnavailable()
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    action=action,
                    resource_type="recording",
                    resource_id=row.id,
                    occurred_at=now,
                    request_id=request_id,
                    correlation_id=correlation_id,
                    metadata_={},
                )
            )

    async def plan(self, call_id):
        async with self.factory() as session:
            call = await session.get(Call, call_id)
            if call is None:
                raise LookupError("Call does not exist.")
            counts = {}
            for label, model in [
                ("transcripts", CallTurn),
                ("checkpoints", ConversationState),
                ("recordings", Recording),
                ("jobs", FollowupJob),
                ("events", CallEvent),
            ]:
                counts[label] = await session.scalar(
                    select(func.count()).select_from(model).where(model.call_id == call_id)
                )
            return {
                "call_id": str(call.id),
                "call_sid": call.provider_call_id or "db_" + str(call.id),
                "status": call.status,
                "counts": counts,
                "shared_caller_profile": call.caller_id is not None,
                "recording_refs": list(
                    await session.scalars(
                        select(Recording.object_key).where(
                            Recording.call_id == call_id, Recording.storage_provider == "local"
                        )
                    )
                ),
            }

    async def expired(self, now, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Retention batch limit must be between 1 and 1000.")
        async with self.factory() as session:
            # Explicit stored expiries own retention; an unset expiry never means delete.
            return list(
                await session.scalars(
                    select(Call.id)
                    .where(
                        or_(
                            Call.retention_until <= now,
                            select(Recording.id)
                            .where(
                                Recording.call_id == Call.id, Recording.retention_until <= now
                            )
                            .exists(),
                            select(CallTurn.id)
                            .where(CallTurn.call_id == Call.id, CallTurn.retention_until <= now)
                            .exists(),
                        ),
                        Call.status.not_in(("pending", "ringing", "in_progress")),
                        ~select(PrivacyTombstone.id)
                        .where(
                            PrivacyTombstone.call_id == Call.id,
                            PrivacyTombstone.cleanup_pending.is_(False),
                        )
                        .exists(),
                    )
                    .order_by(Call.created_at, Call.id)
                    .limit(limit)
                )
            )

    async def dispose(self, call_id, *, action, actor_id, now):
        async with self.factory() as session, session.begin():
            identity = (
                await session.execute(
                    select(Call.id, Call.provider_call_id).where(Call.id == call_id)
                )
            ).first()
            if identity is None:
                raise LookupError("Call does not exist.")
            sid = identity.provider_call_id or "db_" + str(identity.id)
            await lock_privacy_identity(session, sid)
            call = await session.scalar(
                select(Call).where(Call.id == call_id).with_for_update()
            )
            if call.status in {"pending", "ringing", "in_progress"}:
                raise ValueError("Active calls must end before disposal.")
            await session.execute(
                insert(PrivacyTombstone)
                .values(
                    provider_call_id=sid,
                    call_id=call_id,
                    action=action,
                    cleanup_pending=True,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[PrivacyTombstone.call_id],
                    set_={"cleanup_pending": True, "updated_at": now},
                )
            )
            if call.caller_id:
                await session.execute(
                    update(Caller)
                    .where(Caller.id == call.caller_id)
                    .values(
                        name=None,
                        phone_hash=sha256(("disposed:" + str(uuid4())).encode()).hexdigest(),
                        preferred_language=None,
                        city=None,
                        education=None,
                        current_status=None,
                        anonymized_at=now,
                        retention_until=now,
                        updated_at=now,
                    )
                )
                await session.execute(
                    update(Appointment)
                    .where(Appointment.caller_id == call.caller_id)
                    .values(caller_id=None, anonymized_at=now)
                )
            call.deleted_at = now
            call.recording_url = None
            call.caller_id = None
            await session.execute(
                update(CallTurn)
                .where(CallTurn.call_id == call_id)
                .values(transcript=None, transcript_deleted_at=now)
            )
            await session.execute(
                delete(ConversationState).where(ConversationState.call_id == call_id)
            )
            await session.execute(
                update(CallEvent).where(CallEvent.call_id == call_id).values(payload={})
            )
            await session.execute(
                update(FollowupJob)
                .where(FollowupJob.call_id == call_id)
                .values(
                    payload={},
                    deleted_at=now,
                    status="dead_letter",
                    last_error="privacy_disposed",
                    lock_owner=None,
                    locked_at=None,
                    updated_at=now,
                )
            )
            await session.execute(
                update(Recording)
                .where(Recording.call_id == call_id)
                .values(
                    status="deleted",
                    deleted_at=now,
                    deletion_metadata={"cleanup_pending": True},
                )
            )
            await session.execute(
                update(SafetyEvent)
                .where(SafetyEvent.call_id == call_id)
                .values(metadata_={}, deleted_at=now)
            )
            await session.execute(
                update(ProviderUsage)
                .where(ProviderUsage.call_id == call_id)
                .values(provider_metadata={})
            )
            await session.execute(
                update(CallCost).where(CallCost.call_id == call_id).values(metadata_={})
            )
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    action="privacy." + action,
                    resource_type="call",
                    resource_id=call_id,
                    occurred_at=now,
                    metadata_={"managed_scope": True},
                )
            )
            return sid

    async def cleanup_result(self, call_id, *, pending, now):
        async with self.factory() as session, session.begin():
            await session.execute(
                update(PrivacyTombstone)
                .where(PrivacyTombstone.call_id == call_id)
                .values(
                    cleanup_pending=pending,
                    completed_at=None if pending else now,
                    updated_at=now,
                )
            )
            await session.execute(
                update(Recording)
                .where(Recording.call_id == call_id)
                .values(deletion_metadata={"cleanup_pending": pending})
            )
