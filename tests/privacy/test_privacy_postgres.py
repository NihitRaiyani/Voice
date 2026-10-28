"""Real disposable PostgreSQL proofs; never uses the configured working database."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis.aioredis
import pytest
from alembic import command
from pydantic import SecretStr
from roma.core.field_encryption import configure_field_encryption
from roma.core.privacy_settings import PrivacySettings
from roma.repositories.postgres.background_jobs import BackgroundJobStore
from roma.repositories.postgres.models import (
    AuditLog,
    Call,
    Caller,
    CallEvent,
    CallTurn,
    ConversationState,
    FollowupJob,
    PrivacyTombstone,
    Recording,
    Role,
    User,
    UserRole,
)
from roma.repositories.postgres.privacy import SQLPrivacyRepository
from roma.services.postcall_service import PostcallService
from roma.services.privacy_service import PrivacyService
from roma.services.recording_service import RecordingService, RecordingUnavailable
from roma.workers.postcall.job import PostcallJob
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url

__all__ = ["alembic_config", "disposable_postgres_url"]
pytestmark = pytest.mark.postgres


@pytest.fixture
def factory(alembic_config, disposable_postgres_url):
    command.upgrade(alembic_config, "head")
    engine = create_async_engine(disposable_postgres_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    configure_field_encryption(PrivacySettings(_env_file=None))
    asyncio.run(engine.dispose())
    command.downgrade(alembic_config, "base")


def test_orm_encrypts_raw_sql_and_authenticated_roundtrip(factory):
    async def scenario():
        configure_field_encryption(
            PrivacySettings(
                _env_file=None,
                pii_encryption_enabled=True,
                pii_encryption_key=SecretStr("11" * 32),
            )
        )
        now, caller_id, call_id = datetime.now(UTC), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add(Caller(id=caller_id, phone_hash="ab" * 32, name="Synthetic Caller"))
            session.add(
                Call(id=call_id, direction="inbound", status="completed", started_at=now)
            )
        async with factory() as session, session.begin():
            session.add(
                CallTurn(
                    call_id=call_id,
                    turn_number=1,
                    speaker="caller",
                    transcript="Synthetic transcript",
                )
            )
        async with factory() as session:
            assert await session.scalar(select(Caller.name)) == "Synthetic Caller"
            assert await session.scalar(select(CallTurn.transcript)) == "Synthetic transcript"
            raw = await session.scalar(text("SELECT name FROM callers"))
            raw_turn = await session.scalar(text("SELECT transcript FROM call_turns"))
            assert raw.startswith("roma:enc:v1:") and "Synthetic" not in raw
            assert raw_turn.startswith("roma:enc:v1:") and "Synthetic" not in raw_turn

    asyncio.run(scenario())


def test_disposal_scrubs_and_tombstone_blocks_queued_postcall_resurrection(factory, tmp_path):
    async def scenario():
        now = datetime.now(UTC)
        job = PostcallJob(
            "CA_privacy_sql",
            "2026-10-24/CA_privacy_sql.s16le",
            None,
            (now - timedelta(minutes=2)).isoformat(),
            now.isoformat(),
            120,
            90,
            "no_lock",
            caller_digest="cd" * 32,
            recording_consent_at=(now - timedelta(minutes=2)).isoformat(),
            recording_disclosure_version="synthetic-approved-v1",
            recording_retention_until=(now + timedelta(days=1)).isoformat(),
        )
        store = BackgroundJobStore(factory)
        await PostcallService(store).persist(job, "stored")
        async with factory() as session, session.begin():
            call = await session.scalar(select(Call))
            call_id, caller_id = call.id, call.caller_id
            session.add(
                CallTurn(
                    call_id=call_id,
                    turn_number=1,
                    speaker="caller",
                    transcript="private synthetic",
                )
            )
            session.add(
                CallEvent(
                    call_id=call_id,
                    event_type="synthetic",
                    payload={"caller_transcript": "private synthetic"},
                )
            )
            session.add(
                ConversationState(
                    call_id=call_id,
                    schema_version=1,
                    revision=1,
                    policy_version="synthetic",
                    conversation_stage="open",
                    state={"name": "synthetic"},
                    retention_until=now + timedelta(days=1),
                )
            )
            recording = await session.scalar(select(Recording))
            assert recording.consent_at is not None and recording.disclosure_version
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        service = PrivacyService(SQLPrivacyRepository(factory), tmp_path, redis=redis)
        preview = await service.dispose(call_id)
        assert preview["counts"]["recordings"] == 1 and not preview["applied"]
        result = await service.dispose(call_id, apply=True)
        assert result["managed_cleanup_complete"]
        assert call_id not in await SQLPrivacyRepository(factory).expired(
            now + timedelta(days=2)
        )
        await PostcallService(store).persist(job, "stored")
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(Call)) == 1
            assert await session.scalar(select(func.count()).select_from(Caller)) == 1
            assert await session.scalar(select(func.count()).select_from(PrivacyTombstone)) == 1
            assert await session.scalar(select(CallTurn.transcript)) is None
            assert await session.scalar(select(CallEvent.payload)) == {}
            assert (
                await session.scalar(select(func.count()).select_from(ConversationState)) == 0
            )
            assert all(
                row.deleted_at and row.payload == {}
                for row in await session.scalars(select(FollowupJob))
            )
            assert (await session.get(Caller, caller_id)).anonymized_at is not None
            assert await session.scalar(select(Recording.status)) == "deleted"
        await redis.aclose()

    asyncio.run(scenario())


def test_recording_audit_denies_live_role_revoke_retention_and_disposal(factory, tmp_path):
    async def scenario():
        now = datetime.now(UTC)
        call_id, recording_id, user_id, role_id = uuid4(), uuid4(), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add(
                Call(id=call_id, direction="inbound", status="completed", started_at=now)
            )
            session.add(
                User(
                    id=user_id,
                    email="privacy@example.invalid",
                    display_name="Synthetic",
                    password_hash="not-a-credential",
                )
            )
            session.add(Role(id=role_id, name="counsellor"))
        async with factory() as session, session.begin():
            session.add(UserRole(user_id=user_id, role_id=role_id))
            session.add(
                Recording(
                    id=recording_id,
                    call_id=call_id,
                    storage_provider="local",
                    object_key="sample.wav",
                    media_type="audio/wav",
                    status="available",
                    consent_at=now,
                    disclosure_version="synthetic",
                    retention_until=now + timedelta(days=1),
                )
            )
        (tmp_path / "sample.wav").write_bytes(b"RIFFsynthetic")
        service = RecordingService(
            SQLPrivacyRepository(factory),
            tmp_path,
            PrivacySettings(_env_file=None, recording_url_signing_key=SecretStr("22" * 32)),
        )
        issued = await service.issue(recording_id, user_id)
        token = issued["url"].split("token=")[1]
        assert (await service.play(recording_id, user_id, token))[0] == b"RIFFsynthetic"
        async with factory() as session, session.begin():
            user = await session.get(User, user_id)
            user.is_active = False
        with pytest.raises(RecordingUnavailable):
            await service.play(recording_id, user_id, token)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(AuditLog)) == 2

    asyncio.run(scenario())


def test_database_only_call_can_be_disposed_and_child_expiry_is_discoverable(factory, tmp_path):
    async def scenario():
        now, call_id = datetime.now(UTC), uuid4()
        async with factory() as session, session.begin():
            session.add(
                Call(id=call_id, direction="inbound", status="completed", started_at=now)
            )
        async with factory() as session, session.begin():
            session.add(
                CallTurn(
                    call_id=call_id,
                    turn_number=1,
                    speaker="caller",
                    transcript="synthetic",
                    retention_until=now - timedelta(seconds=1),
                )
            )
        repo = SQLPrivacyRepository(factory)
        assert call_id in await repo.expired(now)
        result = await PrivacyService(repo, tmp_path).dispose(call_id, apply=True)
        assert result["applied"] and result["cleanup_pending"] == ["redis"]
        async with factory() as session:
            assert await session.scalar(select(CallTurn.transcript)) is None

    asyncio.run(scenario())


def test_encryption_backfill_is_explicit_bounded_and_repeatable(factory):
    from scripts.privacy import backfill

    async def scenario():
        settings = PrivacySettings(
            _env_file=None, pii_encryption_enabled=True, pii_encryption_key=SecretStr("33" * 32)
        )
        async with factory() as session, session.begin():
            await session.execute(
                text("INSERT INTO callers (phone_hash,name) VALUES (:digest,:name)"),
                {"digest": "ef" * 32, "name": "Synthetic legacy"},
            )
        configure_field_encryption(settings)
        result = await backfill(factory, apply=False, limit=1, settings=settings)
        assert not result["applied"] and result["plaintext_rows_in_batch"]["callers"] == 1
        async with factory() as session:
            assert await session.scalar(text("SELECT name FROM callers")) == "Synthetic legacy"
        await backfill(factory, apply=True, limit=1, settings=settings)
        async with factory() as session:
            assert await session.scalar(select(Caller.name)) == "Synthetic legacy"
            assert (await session.scalar(text("SELECT name FROM callers"))).startswith(
                "roma:enc:v1:"
            )
        result = await backfill(factory, apply=True, limit=1, settings=settings)
        assert result["plaintext_rows_in_batch"]["callers"] == 0

    asyncio.run(scenario())


def test_concurrent_backfill_cannot_resurrect_disposed_pii(factory, monkeypatch):
    from scripts.privacy import backfill
    from sqlalchemy.ext.asyncio import AsyncSession

    async def scenario():
        now, caller_id, call_id = datetime.now(UTC), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add(
                Caller(id=caller_id, phone_hash="45" * 32, name="Synthetic legacy private")
            )
        async with factory() as session, session.begin():
            session.add(
                Call(
                    id=call_id,
                    caller_id=caller_id,
                    provider_call_id="CA_backfill_race",
                    direction="inbound",
                    status="completed",
                    started_at=now,
                )
            )
        settings = PrivacySettings(
            _env_file=None, pii_encryption_enabled=True, pii_encryption_key=SecretStr("55" * 32)
        )
        configure_field_encryption(settings)
        selected, release = asyncio.Event(), asyncio.Event()
        original = AsyncSession.execute
        paused = False

        async def execute(session, statement, *args, **kwargs):
            nonlocal paused
            result = await original(session, statement, *args, **kwargs)
            columns = getattr(statement, "selected_columns", ())
            if not paused and [column.key for column in columns] == ["id", "name"]:
                paused = True
                selected.set()
                await release.wait()
            return result

        monkeypatch.setattr(AsyncSession, "execute", execute)
        backfill_task = asyncio.create_task(
            backfill(factory, apply=True, limit=10, settings=settings)
        )
        await asyncio.wait_for(selected.wait(), 2)
        deletion_task = asyncio.create_task(
            SQLPrivacyRepository(factory).dispose(
                call_id, action="delete", actor_id=None, now=now
            )
        )
        await asyncio.wait({deletion_task}, timeout=0.1)
        release.set()
        await backfill_task
        await deletion_task
        async with factory() as session:
            assert await session.scalar(select(Caller.name)) is None
            assert (await session.get(Call, call_id)).deleted_at is not None

    asyncio.run(scenario())


def test_subject_erasure_is_atomic_across_siblings_and_retryable_after_detachment(
    factory, tmp_path
):
    from decimal import Decimal

    from roma.repositories.postgres.models import CallCost, RetrievalLog

    async def scenario():
        now, caller_id, other_id = datetime.now(UTC), uuid4(), uuid4()
        first, second, unrelated = uuid4(), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add_all(
                [
                    Caller(id=caller_id, phone_hash="ab" * 32, name="Synthetic Subject"),
                    Caller(id=other_id, phone_hash="ef" * 32, name="Keep"),
                ]
            )
        async with factory() as session, session.begin():
            session.add_all(
                [
                    Call(
                        id=first,
                        caller_id=caller_id,
                        provider_call_id="CA_subject_first",
                        direction="inbound",
                        status="completed",
                        started_at=now,
                    ),
                    Call(
                        id=second,
                        caller_id=caller_id,
                        provider_call_id="CA_subject_second",
                        direction="inbound",
                        status="in_progress",
                        started_at=now,
                    ),
                    Call(
                        id=unrelated,
                        caller_id=other_id,
                        provider_call_id="CA_subject_other",
                        direction="inbound",
                        status="completed",
                        started_at=now,
                    ),
                ]
            )
        async with factory() as session, session.begin():
            for call_id in (first, second, unrelated):
                session.add(
                    CallTurn(
                        call_id=call_id,
                        turn_number=1,
                        speaker="caller",
                        transcript="synthetic private",
                    )
                )
            session.add(
                RetrievalLog(
                    call_id=first,
                    request_id=uuid4(),
                    fallback_used=False,
                    route="knowledge",
                    category="course",
                    outcome="eligible",
                    eligible_count=1,
                    latency_ms=12,
                )
            )
            session.add(
                CallCost(
                    call_id=first,
                    provider="synthetic",
                    service="llm",
                    quantity=Decimal("1"),
                    unit="request",
                    unit_price=Decimal("1.2500"),
                    pricing_version="test",
                    idempotency_key="synthetic-subject-cost",
                    amount=Decimal("1.2500"),
                    currency="INR",
                    metadata_={"private": "synthetic"},
                )
            )
        repo = SQLPrivacyRepository(factory)
        service = PrivacyService(repo, tmp_path)
        preview = await service.dispose_subject(caller_id)
        assert preview["counts"]["calls"] == 2 and not preview["applied"]
        with pytest.raises(ValueError, match="Active calls"):
            await service.dispose_subject(caller_id, apply=True)
        async with factory() as session, session.begin():
            assert await session.scalar(select(func.count()).select_from(PrivacyTombstone)) == 0
            assert (await session.get(Caller, caller_id)).name == "Synthetic Subject"
            (await session.get(Call, second)).status = "completed"
        result = await service.dispose_subject(caller_id, apply=True)
        assert result["applied"] and not result["managed_cleanup_complete"]
        assert all(item["cleanup_pending"] == ["redis"] for item in result["calls"])
        async with factory() as session:
            assert (await session.get(Caller, caller_id)).anonymized_at is not None
            assert (await session.get(Caller, other_id)).name == "Keep"
            assert (await session.get(Call, unrelated)).deleted_at is None
            assert (await session.get(Call, first)).caller_id is None
            assert (await session.get(Call, second)).caller_id is None
            assert (
                await session.scalar(
                    select(CallTurn.transcript).where(CallTurn.call_id == unrelated)
                )
                == "synthetic private"
            )
            assert list(
                await session.scalars(
                    select(CallTurn.transcript).where(CallTurn.call_id.in_([first, second]))
                )
            ) == [None, None]
            retrieval = await session.scalar(select(RetrievalLog))
            assert retrieval.call_id is None and retrieval.turn_id is None
            finance = await session.scalar(select(CallCost))
            assert (
                finance.amount == Decimal("1.2500")
                and finance.currency == "INR"
                and finance.metadata_ == {}
            )
        service.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        retry_preview = await service.dispose_subject(caller_id)
        assert retry_preview["counts"]["calls"] == 2
        assert (await service.dispose_subject(caller_id, apply=True))[
            "managed_cleanup_complete"
        ]
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(PrivacyTombstone)) == 2
        await service.redis.aclose()

    asyncio.run(scenario())


def test_disposal_detaches_retrieval_attributed_only_by_turn(factory, tmp_path):
    from roma.repositories.postgres.models import RetrievalLog

    async def scenario():
        now, call_id, turn_id = datetime.now(UTC), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add(
                Call(
                    id=call_id,
                    provider_call_id="CA_retrieval_turn_only",
                    direction="inbound",
                    status="completed",
                    started_at=now,
                )
            )
        async with factory() as session, session.begin():
            session.add(
                CallTurn(
                    id=turn_id,
                    call_id=call_id,
                    turn_number=1,
                    speaker="caller",
                    transcript="private synthetic",
                )
            )
        async with factory() as session, session.begin():
            session.add(
                RetrievalLog(
                    turn_id=turn_id,
                    request_id=uuid4(),
                    fallback_used=False,
                    route="knowledge",
                    category="course",
                    outcome="eligible",
                    eligible_count=1,
                    latency_ms=12,
                )
            )
        await PrivacyService(SQLPrivacyRepository(factory), tmp_path).dispose(
            call_id, apply=True
        )
        async with factory() as session:
            row = await session.scalar(select(RetrievalLog))
            assert row.call_id is None and row.turn_id is None

    asyncio.run(scenario())


def test_subject_row_fence_blocks_new_associations_without_blocking_other_callers(factory):
    async def scenario():
        now, caller_id, other_id, call_id = datetime.now(UTC), uuid4(), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add_all(
                [
                    Caller(id=caller_id, phone_hash="ac" * 32),
                    Caller(id=other_id, phone_hash="bd" * 32),
                ]
            )
        async with factory() as session, session.begin():
            session.add(
                Call(
                    id=call_id,
                    caller_id=caller_id,
                    provider_call_id="CA_fence",
                    direction="inbound",
                    status="completed",
                    started_at=now,
                )
            )
        entered, release, inserting = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class PausedRepository(SQLPrivacyRepository):
            async def _dispose_call(self, *args, **kwargs):
                entered.set()
                await release.wait()
                return await super()._dispose_call(*args, **kwargs)

        async def associate(subject_id, sid, *, signal=False):
            async with factory() as session, session.begin():
                if signal:
                    inserting.set()
                session.add(
                    Call(
                        caller_id=subject_id,
                        provider_call_id=sid,
                        direction="inbound",
                        status="completed",
                        started_at=now,
                    )
                )
                await session.flush()

        repo = PausedRepository(factory)
        disposing = asyncio.create_task(
            repo.dispose_subject(
                caller_id, call_ids=[call_id], action="delete", actor_id=None, now=now
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        associating = asyncio.create_task(associate(caller_id, "CA_after_fence", signal=True))
        try:
            await asyncio.wait_for(inserting.wait(), timeout=2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(associating), timeout=0.1)
            # Only the subject row is fenced; an unrelated caller remains writable.
            await asyncio.wait_for(associate(other_id, "CA_other_fence"), timeout=2)
        finally:
            release.set()
            await asyncio.wait_for(disposing, timeout=3)
            await asyncio.wait_for(associating, timeout=3)
        async with factory() as session:
            assert (await session.get(Call, call_id)).deleted_at is not None
            # New contact linearizes after disposal; this API does not suppress it.
            later = await session.scalar(
                select(Call).where(Call.provider_call_id == "CA_after_fence")
            )
            assert later.deleted_at is None

    asyncio.run(scenario())


def test_subject_erasure_rejects_changed_graph_without_partial_sql_disposal(factory):
    async def scenario():
        now, caller_id, first, second = datetime.now(UTC), uuid4(), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add(Caller(id=caller_id, phone_hash="ce" * 32, name="Keep Until Retry"))
        async with factory() as session, session.begin():
            session.add(
                Call(
                    id=first,
                    caller_id=caller_id,
                    provider_call_id="CA_changed_first",
                    direction="inbound",
                    status="completed",
                    started_at=now,
                )
            )
        repo = SQLPrivacyRepository(factory)
        preview = await repo.plan_subject(caller_id)
        async with factory() as session, session.begin():
            session.add(
                Call(
                    id=second,
                    caller_id=caller_id,
                    provider_call_id="CA_changed_second",
                    direction="inbound",
                    status="completed",
                    started_at=now,
                )
            )
        with pytest.raises(ValueError, match="graph changed"):
            await repo.dispose_subject(
                caller_id, call_ids=preview["call_ids"], action="delete", actor_id=None, now=now
            )
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(PrivacyTombstone)) == 0
            assert (await session.get(Caller, caller_id)).name == "Keep Until Retry"
            assert (await session.get(Call, first)).deleted_at is None
            assert (await session.get(Call, second)).deleted_at is None

    asyncio.run(scenario())


@pytest.mark.parametrize("include_turn", [False, True])
def test_disposal_rejects_delayed_retrieval_audit_relinking(factory, tmp_path, include_turn):
    from roma.domain.knowledge.governance import KnowledgeError
    from roma.repositories.postgres.knowledge import KnowledgeRepository
    from roma.repositories.postgres.models import RetrievalLog
    from roma.schemas.knowledge import RetrievalMetadata

    async def scenario():
        now, call_id, turn_id = datetime.now(UTC), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add(
                Call(
                    id=call_id,
                    provider_call_id="CA_delayed_audit",
                    direction="inbound",
                    status="completed",
                    started_at=now,
                )
            )
        async with factory() as session, session.begin():
            session.add(
                CallTurn(
                    id=turn_id,
                    call_id=call_id,
                    turn_number=1,
                    speaker="caller",
                    transcript="private synthetic",
                )
            )
        await PrivacyService(SQLPrivacyRepository(factory), tmp_path).dispose(
            call_id, apply=True
        )
        metadata = RetrievalMetadata(
            call_id=call_id,
            turn_id=turn_id if include_turn else None,
            request_id=uuid4(),
            route="knowledge",
            category="course",
            outcome="no_evidence",
            fallback_used=True,
            eligible_count=0,
            latency_ms=12,
        )
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_EVIDENCE_MISMATCH"):
            await KnowledgeRepository(factory).record_retrieval(metadata, now=now)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(RetrievalLog)) == 0

    asyncio.run(scenario())


def test_subject_disposal_serializes_delayed_retrieval_audit_admission(factory, tmp_path):
    from roma.domain.knowledge.governance import KnowledgeError
    from roma.repositories.postgres.knowledge import KnowledgeRepository
    from roma.repositories.postgres.models import RetrievalLog
    from roma.schemas.knowledge import RetrievalMetadata

    async def scenario():
        now, caller_id, call_id = datetime.now(UTC), uuid4(), uuid4()
        async with factory() as session, session.begin():
            session.add(Caller(id=caller_id, phone_hash="da" * 32))
        async with factory() as session, session.begin():
            session.add(
                Call(
                    id=call_id,
                    caller_id=caller_id,
                    provider_call_id="CA_subject_audit_fence",
                    direction="inbound",
                    status="completed",
                    started_at=now,
                )
            )
        entered, release, auditing = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class PausedRepository(SQLPrivacyRepository):
            async def _dispose_call(self, *args, **kwargs):
                entered.set()
                await release.wait()
                return await super()._dispose_call(*args, **kwargs)

        async def record():
            auditing.set()
            await KnowledgeRepository(factory).record_retrieval(
                RetrievalMetadata(
                    call_id=call_id,
                    request_id=uuid4(),
                    route="knowledge",
                    category="course",
                    outcome="no_evidence",
                    fallback_used=True,
                    eligible_count=0,
                    latency_ms=12,
                ),
                now=now,
            )

        service = PrivacyService(PausedRepository(factory), tmp_path)
        disposing = asyncio.create_task(service.dispose_subject(caller_id, apply=True))
        await asyncio.wait_for(entered.wait(), timeout=2)
        audit = asyncio.create_task(record())
        try:
            await asyncio.wait_for(auditing.wait(), timeout=2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(audit), timeout=0.1)
        finally:
            release.set()
            await asyncio.wait_for(disposing, timeout=3)
            with pytest.raises(KnowledgeError, match="KNOWLEDGE_EVIDENCE_MISMATCH"):
                await asyncio.wait_for(audit, timeout=3)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(RetrievalLog)) == 0

    asyncio.run(scenario())
