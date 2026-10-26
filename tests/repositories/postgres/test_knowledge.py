import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from roma.domain.knowledge.governance import KnowledgeError
from roma.repositories.postgres.knowledge import KnowledgeRepository
from roma.repositories.postgres.models import User
from roma.repositories.postgres.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    RetrievalLog,
)
from roma.schemas.knowledge import UploadKnowledgeRequest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError, IntegrityError
from tests.repositories.postgres.test_repositories import (
    alembic_config,
    disposable_postgres_url,
    session_factory,
)

__all__ = ["session_factory", "alembic_config", "disposable_postgres_url"]
pytestmark = pytest.mark.postgres
NOW = datetime(2026, 10, 24, tzinfo=UTC)


async def seed(factory):
    owner, admin = uuid4(), uuid4()
    async with factory() as session, session.begin():
        for identity in [owner, admin]:
            session.add(
                User(
                    id=identity,
                    email=f"{identity}@example.invalid",
                    display_name="Synthetic",
                    password_hash="not-a-login",
                )
            )
    return owner, admin


def request(owner, **changes):
    values = dict(
        source_key="course/1",
        source_reference="policy/1",
        title="Course modules",
        version=1,
        category="course",
        language="hi-en",
        owner_id=owner,
        effective_from=NOW - timedelta(days=1),
        effective_until=NOW + timedelta(days=1),
        content="Practical campaign work aur live projects se portfolio banta hai.",
    )
    return UploadKnowledgeRequest(**(values | changes))


def test_sql_lifecycle_eligibility_scope_and_immutable_approval(session_factory):
    async def run():
        owner, admin = await seed(session_factory)
        repo = KnowledgeRepository(session_factory)
        row = await repo.create(request(owner), actor_id=admin)
        assert not await repo.eligible_documents(now=NOW, category="course", language="hi-en")
        row = await repo.transition(
            row.id, "approve", actor_id=admin, now=NOW, reference="review/1"
        )
        assert row.approved_by == admin and row.owner_id == owner
        assert not await repo.eligible_documents(now=NOW, category="course", language="hi-en")
        row = await repo.transition(
            row.id, "activate", actor_id=admin, now=NOW, expected_version=1
        )
        assert [
            entry.id
            for entry in await repo.eligible_documents(
                now=NOW, category="course", language="hi-en"
            )
        ] == [row.id]
        assert not await repo.eligible_documents(
            now=NOW + timedelta(days=1), category="course", language="hi-en"
        )
        assert not await repo.eligible_documents(now=NOW, category="course", language="gu")
        with pytest.raises(DBAPIError):
            async with session_factory() as session, session.begin():
                await session.execute(
                    update(KnowledgeDocument)
                    .where(KnowledgeDocument.id == row.id)
                    .values(content="Unreviewed edit")
                )
        with pytest.raises(IntegrityError):
            await repo.create(request(owner), actor_id=admin)
        with pytest.raises(KnowledgeError):
            await repo.transition(
                row.id, "archive", actor_id=admin, now=NOW, expected_version=2
            )
        await repo.transition(row.id, "archive", actor_id=admin, now=NOW, expected_version=1)
        assert not await repo.eligible_documents(now=NOW, category="course", language="hi-en")

    asyncio.run(run())


def test_metadata_has_no_raw_query_and_vectors_have_scope_provenance():
    assert {
        "language",
        "metadata_json",
        "document_id",
        "embedding_model",
        "embedding_revision",
        "embedding_dimensions",
        "embedded_at",
    } <= set(KnowledgeChunk.__table__.c.keys())
    assert "embedding" in KnowledgeChunk.__table__.c
    assert {"call_id", "turn_id", "chunk_ids", "scores", "fallback_used"} <= set(
        RetrievalLog.__table__.c.keys()
    )
    assert not {"query", "query_text", "transcript", "phone", "prompt"} & set(
        RetrievalLog.__table__.c.keys()
    )


def test_new_active_version_archives_previous_source_atomically(session_factory):
    async def run():
        owner, admin = await seed(session_factory)
        repo = KnowledgeRepository(session_factory)
        first = await repo.create(request(owner), actor_id=admin)
        await repo.transition(
            first.id, "approve", actor_id=admin, now=NOW, reference="review/1"
        )
        await repo.transition(first.id, "activate", actor_id=admin, now=NOW, expected_version=1)
        second = await repo.create(request(owner, version=2), actor_id=admin)
        await repo.transition(
            second.id, "approve", actor_id=admin, now=NOW, reference="review/2"
        )
        await repo.transition(
            second.id, "activate", actor_id=admin, now=NOW, expected_version=2
        )
        rows = await repo.eligible_documents(now=NOW, category="course", language="hi-en")
        assert [row.id for row in rows] == [second.id]
        async with session_factory() as session:
            original = await session.get(KnowledgeDocument, first.id)
            assert original.status == "archived" and original.archived_at == NOW

    asyncio.run(run())


def test_retrieval_log_accepts_only_approved_matching_chunk_provenance(session_factory):
    from hashlib import sha256

    from roma.schemas.knowledge import RetrievalMetadata
    from sqlalchemy import select

    async def run():
        owner, admin = await seed(session_factory)
        repo = KnowledgeRepository(session_factory)
        row = await repo.create(request(owner), actor_id=admin)
        await repo.transition(row.id, "approve", actor_id=admin, now=NOW, reference="review/1")
        await repo.transition(row.id, "activate", actor_id=admin, now=NOW, expected_version=1)
        chunk_id = uuid4()
        content = "Practical campaign work aur live projects se portfolio banta hai."
        async with session_factory() as session, session.begin():
            session.add(
                KnowledgeChunk(
                    id=chunk_id,
                    document_id=row.id,
                    ordinal=0,
                    language="hi-en",
                    metadata_json={"source_section": "intro/1", "document_version": 1},
                    content=content,
                    content_hash=sha256(content.encode()).hexdigest(),
                )
            )
        metadata = RetrievalMetadata(
            request_id=uuid4(),
            route="knowledge",
            category="course",
            outcome="eligible",
            eligible_count=1,
            latency_ms=1,
            selected_document_id=row.id,
            chunk_ids=[chunk_id],
            scores=[0.8],
            fallback_used=False,
        )
        await repo.record_retrieval(metadata, now=NOW)
        async with session_factory() as session:
            stored = (await session.scalars(select(RetrievalLog))).one()
            assert stored.chunk_ids == [chunk_id] and stored.scores == [0.8]
        await repo.transition(row.id, "archive", actor_id=admin, now=NOW, expected_version=1)
        with pytest.raises(KnowledgeError):
            await repo.record_retrieval(metadata, now=NOW)

    asyncio.run(run())
