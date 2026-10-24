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
