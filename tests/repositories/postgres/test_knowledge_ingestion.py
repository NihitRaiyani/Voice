import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from roma.core.ai_contracts import EmbeddingIdentity
from roma.domain.knowledge.ingestion import chunk_document
from roma.repositories.postgres.knowledge import KnowledgeRepository
from roma.repositories.postgres.knowledge_ingestion import KnowledgeIngestionRepository
from tests.repositories.postgres.test_knowledge import (
    NOW,
    alembic_config,
    disposable_postgres_url,
    request,
    seed,
    session_factory,
)

__all__ = ["session_factory", "alembic_config", "disposable_postgres_url"]
pytestmark = pytest.mark.postgres


def test_duplicate_ingestion_and_expired_claim_cannot_complete(session_factory):
    async def run():
        owner, admin = await seed(session_factory)
        document = await KnowledgeRepository(session_factory).create(
            request(owner), actor_id=admin
        )
        repo = KnowledgeIngestionRepository(session_factory)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")
        first = await repo.enqueue_ingestion(
            document.id, document.content_hash, "pipeline-v1", identity=identity, now=NOW
        )
        duplicate = await repo.enqueue_ingestion(
            document.id, document.content_hash, "pipeline-v1", identity=identity, now=NOW
        )
        assert duplicate.id == first.id
        claim = await repo.claim_ingestion(first.id, "old", NOW)
        assert claim is not None
        assert await repo.claim_ingestion(first.id, "other", NOW) is None
        later = NOW + timedelta(seconds=301)
        successor = await repo.claim_ingestion(first.id, "new", later)
        assert successor is not None and successor.attempts == 2
        assert (
            await repo.fail_ingestion(claim, "old", "ingestion_failed", now=later)
            == "lost_lease"
        )
        assert (await repo.load_ingestion_document(successor)).id == document.id
        assert (
            await repo.fail_ingestion(
                successor, "new", "invalid_content", permanent=True, now=later
            )
            == "dead_letter"
        )
        assert (
            await repo.claim_ingestion(first.id, "third", later + timedelta(seconds=301))
            is None
        )

    asyncio.run(run())


def test_quality_checked_chunks_activate_and_exact_search_scope(session_factory):
    from hashlib import sha256

    from roma.domain.knowledge.governance import KnowledgeError
    from roma.domain.knowledge.retrieval import RetrievalFilters
    from roma.repositories.postgres.knowledge_search import KnowledgeSearchRepository

    async def run():
        owner, admin = await seed(session_factory)
        repo = KnowledgeIngestionRepository(session_factory)
        governance = KnowledgeRepository(session_factory)
        document = await governance.create(request(owner), actor_id=admin)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")
        job = await repo.enqueue_ingestion(
            document.id, document.content_hash, "pipeline-v1", identity=identity, now=NOW
        )
        await governance.transition(
            document.id, "approve", actor_id=admin, now=NOW, reference="review/1"
        )
        with pytest.raises(KnowledgeError):
            await governance.transition(
                document.id, "activate", actor_id=admin, now=NOW, expected_version=1
            )
        claim = await repo.claim_ingestion(job.id, "worker", NOW)
        content = document.content
        chunks = [
            dict(
                ordinal=0,
                content=content,
                content_hash=sha256(content.encode()).hexdigest(),
                language=document.language,
                source_section=chunk_document(document.content, language=document.language)[
                    0
                ].source_section,
                vector=(1.0, 0.0),
            )
        ]
        assert await repo.complete_ingestion(
            claim, "worker", document.content_hash, chunks, identity, now=NOW
        )
        await governance.transition(
            document.id, "activate", actor_id=admin, now=NOW, expected_version=1
        )
        search = KnowledgeSearchRepository(session_factory)
        filters = RetrievalFilters("course", "hi-en", allow_english=False)
        found = await search.search_chunks((1.0, 0.0), identity, filters, NOW, 5, 0.5)
        assert (
            len(found) == 1
            and found[0].document_id == document.id
            and found[0].score == pytest.approx(1.0)
        )
        assert not await search.search_chunks((0.0, 1.0), identity, filters, NOW, 5, 0.5)
        assert not await search.search_chunks(
            (1.0, 0.0), identity, RetrievalFilters("policy", "hi-en"), NOW, 5, 0.5
        )
        assert not await search.search_chunks(
            (1.0, 0.0),
            EmbeddingIdentity("other", "1", 2, "sha256-v1", ""),
            filters,
            NOW,
            5,
            0.5,
        )
        assert not await search.search_chunks(
            (1.0, 0.0), identity, filters, NOW + timedelta(days=2), 5, 0.5
        )
        assert await search.revalidate_evidence((found[0].chunk_id,), filters, identity, NOW)
        await governance.transition(
            document.id, "archive", actor_id=admin, now=NOW, expected_version=1
        )
        assert not await search.revalidate_evidence(
            (found[0].chunk_id,), filters, identity, NOW
        )

    asyncio.run(run())


def test_reembedding_preserves_trace_ids_and_failed_version_keeps_active_source(
    session_factory,
):
    from hashlib import sha256

    from roma.domain.knowledge.governance import KnowledgeError
    from roma.domain.knowledge.retrieval import RetrievalFilters
    from roma.repositories.postgres.knowledge_search import KnowledgeSearchRepository

    async def run():
        owner, admin = await seed(session_factory)
        repo = KnowledgeIngestionRepository(session_factory)
        governance = KnowledgeRepository(session_factory)
        search = KnowledgeSearchRepository(session_factory)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")
        first = await governance.create(request(owner), actor_id=admin, ingestion_required=True)

        async def ingest(document, pipeline, vector):
            job = await repo.enqueue_ingestion(
                document.id, document.content_hash, pipeline, identity=identity, now=NOW
            )
            claimed = await repo.claim_ingestion(job.id, "worker", NOW)
            chunk = dict(
                ordinal=0,
                content=document.content,
                content_hash=sha256(document.content.encode()).hexdigest(),
                language=document.language,
                source_section=chunk_document(document.content, language=document.language)[
                    0
                ].source_section,
                vector=vector,
            )
            assert await repo.complete_ingestion(
                claimed, "worker", document.content_hash, [chunk], identity, now=NOW
            )

        await ingest(first, "pipeline-v1", (1.0, 0.0))
        await governance.transition(
            first.id, "approve", actor_id=admin, now=NOW, reference="review/1"
        )
        await governance.transition(
            first.id, "activate", actor_id=admin, now=NOW, expected_version=1
        )
        filters = RetrievalFilters("course", "hi-en", allow_english=False)
        before = await search.search_chunks((1.0, 0.0), identity, filters, NOW, 5, -1.0)
        await ingest(first, "pipeline-v2", (0.5, 0.5))
        after = await search.search_chunks((1.0, 0.0), identity, filters, NOW, 5, -1.0)
        assert [item.chunk_id for item in before] == [item.chunk_id for item in after]
        second = await governance.create(
            request(owner, version=2), actor_id=admin, ingestion_required=True
        )
        await governance.transition(
            second.id, "approve", actor_id=admin, now=NOW, reference="review/2"
        )
        with pytest.raises(KnowledgeError):
            await governance.transition(
                second.id, "activate", actor_id=admin, now=NOW, expected_version=2
            )
        assert (await search.search_chunks((1.0, 0.0), identity, filters, NOW, 5, -1.0))[
            0
        ].document_id == first.id

    asyncio.run(run())


def test_sql_cannot_bypass_managed_ingestion_quality_gate(session_factory):
    from roma.repositories.postgres.models.knowledge import KnowledgeDocument
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    async def run():
        owner, admin = await seed(session_factory)
        governance = KnowledgeRepository(session_factory)
        document = await governance.create(
            request(owner), actor_id=admin, ingestion_required=True
        )
        await governance.transition(
            document.id, "approve", actor_id=admin, now=NOW, reference="review/1"
        )
        with pytest.raises(DBAPIError):
            async with session_factory() as session, session.begin():
                await session.execute(
                    update(KnowledgeDocument)
                    .where(KnowledgeDocument.id == document.id)
                    .values(status="active", activated_at=NOW)
                )
        with pytest.raises(DBAPIError):
            async with session_factory() as session, session.begin():
                await session.execute(
                    update(KnowledgeDocument)
                    .where(KnowledgeDocument.id == document.id)
                    .values(ingestion_required=False)
                )

    asyncio.run(run())


def test_exact_search_filters_before_top_k_and_rejects_tampered_chunks(session_factory):
    from hashlib import sha256

    from roma.domain.knowledge.retrieval import RetrievalFilters
    from roma.repositories.postgres.knowledge_search import KnowledgeSearchRepository
    from roma.repositories.postgres.models.knowledge import KnowledgeChunk
    from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
    from sqlalchemy import update
    from tests.repositories.postgres.test_repositories import _seed_reference_data

    async def run():
        owner, admin = await seed(session_factory)
        async with PostgresUnitOfWork(session_factory) as uow:
            _, branch, course, _, _ = await _seed_reference_data(uow)
            await uow.commit()
        async with PostgresUnitOfWork(session_factory) as uow:
            _, other_branch, other_course, _, _ = await _seed_reference_data(uow)
            await uow.commit()
        repo = KnowledgeIngestionRepository(session_factory)
        governance = KnowledgeRepository(session_factory)
        search = KnowledgeSearchRepository(session_factory)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")

        async def ingest(source, vector, **changes):
            document = await governance.create(
                request(owner, source_key=source, **changes),
                actor_id=admin,
                ingestion_required=True,
            )
            job = await repo.enqueue_ingestion(
                document.id, document.content_hash, "v1", identity=identity, now=NOW
            )
            claim = await repo.claim_ingestion(job.id, "worker", NOW)
            chunk = dict(
                ordinal=0,
                content=document.content,
                content_hash=sha256(document.content.encode()).hexdigest(),
                language=document.language,
                source_section=chunk_document(document.content, language=document.language)[
                    0
                ].source_section,
                vector=vector,
            )
            await repo.complete_ingestion(
                claim, "worker", document.content_hash, [chunk], identity, now=NOW
            )
            await governance.transition(
                document.id, "approve", actor_id=admin, now=NOW, reference="review/1"
            )
            await governance.transition(
                document.id,
                "activate",
                actor_id=admin,
                now=NOW,
                expected_version=document.version,
            )
            return document

        wanted = await ingest(
            "course/wanted", (0.8, 0.6), course_id=course, branch_id=branch, version=2
        )
        await ingest(
            "course/wrongbranch",
            (1.0, 0.0),
            course_id=course,
            branch_id=other_branch,
            version=2,
        )
        await ingest(
            "course/wrongcourse",
            (1.0, 0.0),
            course_id=other_course,
            branch_id=branch,
            version=2,
        )
        await ingest(
            "course/wrongversion", (1.0, 0.0), course_id=course, branch_id=branch, version=1
        )
        await ingest(
            "course/english",
            (1.0, 0.0),
            course_id=course,
            branch_id=branch,
            version=2,
            language="en",
        )
        filters = RetrievalFilters("course", "hi-en", course, branch, 2, False)
        found = await search.search_chunks((1.0, 0.0), identity, filters, NOW, 1, -1.0)
        assert len(found) == 1 and found[0].document_id == wanted.id
        assert found[0].score == pytest.approx(0.8)
        assert (
            await search.search_chunks(
                (1.0, 0.0),
                identity,
                RetrievalFilters("course", "hi-en", course, branch, 2, True),
                NOW,
                1,
                -1.0,
            )
        )[0].language == "en"
        async with session_factory() as session, session.begin():
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == found[0].chunk_id)
                .values(content="Unreviewed replacement content.")
            )
        assert not await search.revalidate_evidence(
            (found[0].chunk_id,), filters, identity, NOW
        )
        assert not await search.search_chunks((1.0, 0.0), identity, filters, NOW, 1, -1.0)

    asyncio.run(run())


def test_new_model_generation_fences_older_pipeline_and_mixed_dimensions(session_factory):
    from hashlib import sha256

    from roma.domain.knowledge.retrieval import RetrievalFilters
    from roma.repositories.postgres.knowledge_search import KnowledgeSearchRepository

    async def run():
        owner, admin = await seed(session_factory)
        governance = KnowledgeRepository(session_factory)
        repo = KnowledgeIngestionRepository(session_factory)
        document = await governance.create(
            request(owner), actor_id=admin, ingestion_required=True
        )
        old_identity = EmbeddingIdentity("old-model", "1", 2, "old-preprocess", "")
        new_identity = EmbeddingIdentity("new-model", "2", 3, "new-preprocess", "a" * 64)
        old = await repo.enqueue_ingestion(
            document.id, document.content_hash, "old-pipeline", identity=old_identity, now=NOW
        )
        new = await repo.enqueue_ingestion(
            document.id, document.content_hash, "new-pipeline", identity=new_identity, now=NOW
        )
        old_claim = await repo.claim_ingestion(old.id, "old", NOW)
        new_claim = await repo.claim_ingestion(new.id, "new", NOW)

        def chunks(vector):
            return [
                dict(
                    ordinal=0,
                    content=document.content,
                    content_hash=sha256(document.content.encode()).hexdigest(),
                    language=document.language,
                    source_section=chunk_document(document.content, language=document.language)[
                        0
                    ].source_section,
                    vector=vector,
                )
            ]

        assert await repo.complete_ingestion(
            new_claim,
            "new",
            document.content_hash,
            chunks((1.0, 0.0, 0.0)),
            new_identity,
            now=NOW,
        )
        assert not await repo.complete_ingestion(
            old_claim, "old", document.content_hash, chunks((1.0, 0.0)), old_identity, now=NOW
        )
        assert await repo.claim_ingestion(old.id, "again", NOW + timedelta(seconds=301)) is None
        await governance.transition(
            document.id, "approve", actor_id=admin, now=NOW, reference="review/1"
        )
        await governance.transition(
            document.id, "activate", actor_id=admin, now=NOW, expected_version=1
        )
        search = KnowledgeSearchRepository(session_factory)
        filters = RetrievalFilters("course", "hi-en")
        assert not await search.search_chunks((1.0, 0.0), old_identity, filters, NOW, 5, -1.0)
        assert (
            len(await search.search_chunks((1.0, 0.0, 0.0), new_identity, filters, NOW, 5, 0.5))
            == 1
        )

    asyncio.run(run())


def test_managed_upload_persists_original_and_job_in_one_transaction(session_factory):
    from roma.domain.knowledge.governance import KnowledgeError

    async def run():
        owner, admin = await seed(session_factory)
        repo = KnowledgeIngestionRepository(session_factory)
        governance = KnowledgeRepository(session_factory)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")
        document = await governance.create(
            request(owner),
            actor_id=admin,
            ingestion={"pipeline_identity": "v1", "identity": identity, "now": NOW},
        )
        assert document.ingestion_required
        assert (await governance.get(document.id)).content == document.content
        jobs = await repo.jobs_for_document(document.id)
        assert len(jobs) == 1 and jobs[0].expected_hash == document.content_hash
        assert (
            await repo.enqueue_ingestion(
                document.id, document.content_hash, "v1", identity=identity, now=NOW
            )
        ).id == jobs[0].id
        assert await repo.claim_ingestion(jobs[0].id, "worker", NOW) is not None
        with pytest.raises(KnowledgeError):
            await governance.create(
                request(owner, source_key="course/invalid"),
                actor_id=admin,
                ingestion={
                    "pipeline_identity": "v1",
                    "identity": EmbeddingIdentity("", "1", 2),
                    "now": NOW,
                },
            )
        with pytest.raises(KnowledgeError):
            await governance.get(uuid4())

    asyncio.run(run())


def test_transient_ingestion_failure_dead_letters_after_five_sql_attempts(session_factory):
    async def run():
        owner, admin = await seed(session_factory)
        governance = KnowledgeRepository(session_factory)
        repo = KnowledgeIngestionRepository(session_factory)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")
        document = await governance.create(
            request(owner),
            actor_id=admin,
            ingestion={"pipeline_identity": "v1", "identity": identity, "now": NOW},
        )
        job = (await repo.jobs_for_document(document.id))[0]
        now = NOW
        for attempt in range(1, 6):
            claimed = await repo.claim_ingestion(job.id, f"worker-{attempt}", now)
            assert claimed.attempts == attempt
            status = await repo.fail_ingestion(
                claimed, f"worker-{attempt}", "ingestion_timeout", now=now
            )
            assert status == ("dead_letter" if attempt == 5 else "queued")
            now += timedelta(seconds=301)
        assert await repo.claim_ingestion(job.id, "sixth", now) is None
        stored = (await repo.jobs_for_document(document.id))[0]
        assert stored.status == "dead_letter" and stored.last_error == "ingestion_timeout"
        assert not await governance.eligible_documents(
            now=NOW, category="course", language="hi-en"
        )

    asyncio.run(run())


@pytest.mark.parametrize("tamper", ["content", "omitted", "language", "section", "ordinal"])
def test_initial_ingestion_rejects_noncanonical_evidence(session_factory, tamper):
    from dataclasses import asdict
    from hashlib import sha256

    from roma.domain.knowledge.governance import KnowledgeError
    from roma.repositories.postgres.models.knowledge import KnowledgeChunk
    from sqlalchemy import func, select

    async def run():
        owner, admin = await seed(session_factory)
        governance = KnowledgeRepository(session_factory)
        repo = KnowledgeIngestionRepository(session_factory)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")
        document = await governance.create(
            request(
                owner, content="# Modules\nCampaign practice.\n# Projects\nPortfolio practice."
            ),
            actor_id=admin,
            ingestion={"pipeline_identity": "v1", "identity": identity, "now": NOW},
        )
        job = (await repo.jobs_for_document(document.id))[0]
        claimed = await repo.claim_ingestion(job.id, "worker", NOW)
        chunks = [
            asdict(chunk) | {"vector": (1.0, 0.0)}
            for chunk in chunk_document(document.content, language=document.language)
        ]
        assert len(chunks) == 2
        if tamper == "content":
            chunks[0]["content"] = "Campus mein robotics workshop hota hai."
            chunks[0]["content_hash"] = sha256(chunks[0]["content"].encode()).hexdigest()
        elif tamper == "omitted":
            chunks.pop()
        elif tamper == "language":
            chunks[0]["language"] = "en"
        elif tamper == "section":
            chunks[0]["source_section"] = chunks[1]["source_section"]
        else:
            chunks[0]["ordinal"] = False
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_CHUNK_PROVENANCE"):
            await repo.complete_ingestion(
                claimed, "worker", document.content_hash, chunks, identity, now=NOW
            )
        assert (await repo.jobs_for_document(document.id))[0].status == "processing"
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0

    asyncio.run(run())


@pytest.mark.parametrize("tamper", ["content", "language", "section", "version", "ordinal"])
def test_retrieval_rejects_noncanonical_evidence(session_factory, tamper):
    from dataclasses import asdict
    from hashlib import sha256

    from roma.domain.knowledge.retrieval import RetrievalFilters
    from roma.repositories.postgres.knowledge_search import KnowledgeSearchRepository
    from roma.repositories.postgres.models.knowledge import KnowledgeChunk
    from sqlalchemy import update

    async def run():
        owner, admin = await seed(session_factory)
        governance = KnowledgeRepository(session_factory)
        repo = KnowledgeIngestionRepository(session_factory)
        identity = EmbeddingIdentity("mock-sha256", "1", 2, "sha256-v1", "")
        document = await governance.create(
            request(owner),
            actor_id=admin,
            ingestion={"pipeline_identity": "v1", "identity": identity, "now": NOW},
        )
        job = (await repo.jobs_for_document(document.id))[0]
        claimed = await repo.claim_ingestion(job.id, "worker", NOW)
        chunks = [
            asdict(chunk) | {"vector": (1.0, 0.0)}
            for chunk in chunk_document(document.content, language=document.language)
        ]
        await repo.complete_ingestion(
            claimed, "worker", document.content_hash, chunks, identity, now=NOW
        )
        await governance.transition(
            document.id, "approve", actor_id=admin, now=NOW, reference="review/1"
        )
        await governance.transition(
            document.id, "activate", actor_id=admin, now=NOW, expected_version=1
        )
        search = KnowledgeSearchRepository(session_factory)
        filters = RetrievalFilters("course", "hi-en")
        before = await search.search_chunks((1.0, 0.0), identity, filters, NOW, 5, 0.5)
        assert len(before) == 1
        if tamper == "content":
            forged = "Campus mein robotics workshop hota hai."
            changes = {"content": forged, "content_hash": sha256(forged.encode()).hexdigest()}
        elif tamper == "language":
            changes = {"language": "en"}
        elif tamper == "ordinal":
            changes = {"ordinal": 1}
        else:
            changes = {
                "metadata_json": {
                    "source_section": "section-forged"
                    if tamper == "section"
                    else before[0].source_section,
                    "document_version": 2 if tamper == "version" else 1,
                }
            }
        async with session_factory() as session, session.begin():
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == before[0].chunk_id)
                .values(**changes)
            )
        assert not await search.search_chunks((1.0, 0.0), identity, filters, NOW, 5, 0.5)
        assert not await search.revalidate_evidence(
            (before[0].chunk_id,), filters, identity, NOW
        )

    asyncio.run(run())
