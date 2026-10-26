"""Short fenced ingestion units of work; inference runs outside this repository."""

import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from roma.domain.knowledge import governance
from roma.domain.knowledge.ingestion import chunk_document, matches_canonical_chunk
from roma.repositories.postgres.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestionJob,
)
from roma.repositories.postgres.vector import vector_values
from roma.schemas.knowledge import ChunkMetadata

LEASE_SECONDS = 300
MAX_ATTEMPTS = 5


def identity_fields(identity):
    fields = dict(
        model=identity.model,
        revision=identity.revision,
        dimensions=identity.dimension,
        preprocessing=identity.preprocessing,
        artifacts_sha256=identity.artifacts_sha256,
    )
    if (
        type(fields["dimensions"]) is not int
        or not 1 <= fields["dimensions"] <= 16000
        or any(
            not isinstance(fields[key], str) or not 1 <= len(fields[key]) <= 128
            for key in ["model", "revision", "preprocessing"]
        )
    ):
        raise governance.KnowledgeError("KNOWLEDGE_EMBEDDING_IDENTITY")
    if fields["artifacts_sha256"] and not re.fullmatch(
        "[0-9a-f]{64}", fields["artifacts_sha256"]
    ):
        raise governance.KnowledgeError("KNOWLEDGE_EMBEDDING_IDENTITY")
    return fields


def _identity_matches(job, identity):
    return all(getattr(job, key) == value for key, value in identity_fields(identity).items())


def _ready(now):
    return or_(
        and_(
            KnowledgeIngestionJob.status == "queued", KnowledgeIngestionJob.available_at <= now
        ),
        and_(
            KnowledgeIngestionJob.status == "processing",
            KnowledgeIngestionJob.locked_at <= now - timedelta(seconds=LEASE_SECONDS),
        ),
    )


class KnowledgeIngestionRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def enqueue_ingestion(
        self, document_id, expected_hash, pipeline_identity, *, identity, now=None
    ):
        now = now or datetime.now(UTC)
        fields = identity_fields(identity)
        if (
            not re.fullmatch("[0-9a-f]{64}", expected_hash)
            or not isinstance(pipeline_identity, str)
            or not 1 <= len(pipeline_identity) <= 128
        ):
            raise governance.KnowledgeError()
        async with self.session_factory() as session, session.begin():
            document = await session.scalar(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.id == document_id)
                .with_for_update()
            )
            if document is None:
                raise governance.KnowledgeError("KNOWLEDGE_NOT_FOUND", 404)
            if (
                document.content_hash != expected_hash
                or governance.content_hash(document) != expected_hash
                or document.status == "archived"
            ):
                raise governance.KnowledgeError("KNOWLEDGE_CONTENT_CHANGED", 409)
            document.ingestion_required = True
            await session.execute(
                insert(KnowledgeIngestionJob)
                .values(
                    id=uuid4(),
                    document_id=document_id,
                    expected_hash=expected_hash,
                    pipeline_identity=pipeline_identity,
                    available_at=now,
                    **fields,
                )
                .on_conflict_do_nothing(
                    index_elements=["document_id", "expected_hash", "pipeline_identity"]
                )
            )
            job = await session.scalar(
                select(KnowledgeIngestionJob).where(
                    KnowledgeIngestionJob.document_id == document_id,
                    KnowledgeIngestionJob.expected_hash == expected_hash,
                    KnowledgeIngestionJob.pipeline_identity == pipeline_identity,
                )
            )
            if not _identity_matches(job, identity):
                raise governance.KnowledgeError("KNOWLEDGE_EMBEDDING_IDENTITY", 409)
        return job

    async def jobs_for_document(self, document_id):
        async with self.session_factory() as session:
            return tuple(
                (
                    await session.scalars(
                        select(KnowledgeIngestionJob)
                        .where(KnowledgeIngestionJob.document_id == document_id)
                        .order_by(KnowledgeIngestionJob.created_at, KnowledgeIngestionJob.id)
                    )
                ).all()
            )

    async def dispatchable_ids(self, now, limit=100):
        if not 1 <= limit <= 1000:
            raise governance.KnowledgeError()
        async with self.session_factory() as session:
            return tuple(
                (
                    await session.scalars(
                        select(KnowledgeIngestionJob.id)
                        .where(_ready(now))
                        .order_by(KnowledgeIngestionJob.available_at, KnowledgeIngestionJob.id)
                        .limit(limit)
                    )
                ).all()
            )

    async def claim_ingestion(self, job_id, owner, now):
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128 or now.tzinfo is None:
            raise governance.KnowledgeError()
        async with self.session_factory() as session, session.begin():
            job = await session.scalar(
                select(KnowledgeIngestionJob)
                .where(KnowledgeIngestionJob.id == job_id, _ready(now))
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.last_error, job.lock_owner, job.locked_at = (
                    "dead_letter",
                    "lease_retries_exhausted",
                    None,
                    None,
                )
                return None
            job.status, job.lock_owner, job.locked_at = "processing", owner, now
            job.attempts += 1
            await session.flush()
        return job

    async def load_ingestion_document(self, claimed):
        async with self.session_factory() as session:
            document = await session.get(KnowledgeDocument, claimed.document_id)
            if (
                document is None
                or document.status == "archived"
                or document.content_hash != claimed.expected_hash
                or governance.content_hash(document) != claimed.expected_hash
            ):
                raise governance.KnowledgeError("KNOWLEDGE_CONTENT_CHANGED", 409)
            return document

    async def complete_ingestion(
        self, claimed, owner, expected_hash, chunks, identity, *, now=None
    ):
        now = now or datetime.now(UTC)
        if (
            not chunks
            or len(chunks) > 1000
            or expected_hash != claimed.expected_hash
            or not _identity_matches(claimed, identity)
        ):
            raise governance.KnowledgeError("KNOWLEDGE_INGESTION_INVALID")
        prepared = []
        for ordinal, chunk in enumerate(chunks):
            if (
                chunk["ordinal"] != ordinal
                or not 1 <= len(chunk["content"]) <= 2000
                or chunk["content_hash"] != sha256(chunk["content"].encode()).hexdigest()
            ):
                raise governance.KnowledgeError("KNOWLEDGE_INGESTION_INVALID")
            governance.validate_content(chunk["content"])
            values = vector_values(chunk["vector"])
            if len(values) != identity.dimension:
                raise governance.KnowledgeError("KNOWLEDGE_EMBEDDING_IDENTITY")
            metadata = ChunkMetadata(source_section=chunk["source_section"], document_version=1)
            prepared.append((chunk, values, metadata.source_section))
        async with self.session_factory() as session, session.begin():
            document = await session.scalar(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.id == claimed.document_id)
                .with_for_update()
            )
            job = await self._owned(session, claimed, owner, now)
            if job is None:
                return False
            if (
                document is None
                or document.status == "archived"
                or document.content_hash != expected_hash
                or governance.content_hash(document) != expected_hash
                or not _identity_matches(job, identity)
            ):
                raise governance.KnowledgeError("KNOWLEDGE_CONTENT_CHANGED", 409)
            try:
                canonical = chunk_document(document.content, language=document.language)
            except ValueError:
                raise governance.KnowledgeError("KNOWLEDGE_CHUNK_PROVENANCE", 409) from None
            if len(prepared) != len(canonical) or any(
                not matches_canonical_chunk(
                    canonical,
                    ordinal=chunk["ordinal"],
                    content=chunk["content"],
                    content_hash=chunk["content_hash"],
                    language=chunk["language"],
                    source_section=section,
                )
                for chunk, _, section in prepared
            ):
                raise governance.KnowledgeError("KNOWLEDGE_CHUNK_PROVENANCE", 409)
            # A new pipeline cannot replace a newer generation after losing the race.
            newer = await session.scalar(
                select(KnowledgeIngestionJob.id).where(
                    KnowledgeIngestionJob.document_id == document.id,
                    KnowledgeIngestionJob.status == "succeeded",
                    KnowledgeIngestionJob.created_at > job.created_at,
                )
            )
            if newer is not None:
                job.status, job.last_error = "dead_letter", "superseded_generation"
                job.lock_owner, job.locked_at = None, None
                return False
            existing = (
                await session.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == document.id)
                    .order_by(KnowledgeChunk.ordinal)
                    .with_for_update()
                )
            ).all()
            if existing and (
                len(existing) != len(prepared)
                or any(
                    old.ordinal != chunk["ordinal"]
                    or old.content != chunk["content"]
                    or old.content_hash != chunk["content_hash"]
                    or old.metadata_json
                    != {"source_section": section, "document_version": document.version}
                    for old, (chunk, _, section) in zip(existing, prepared, strict=True)
                )
            ):
                # Existing retrieval IDs retain immutable content. A changed chunking
                # needs a newly approved document version, not rewritten history.
                raise governance.KnowledgeError("KNOWLEDGE_CHUNK_VERSION_REQUIRED", 409)
            for index, (chunk, values, section) in enumerate(prepared):
                row = (
                    existing[index]
                    if existing
                    else KnowledgeChunk(
                        id=uuid4(),
                        document_id=document.id,
                        ordinal=chunk["ordinal"],
                        content=chunk["content"],
                        content_hash=chunk["content_hash"],
                        language=document.language,
                        metadata_json={
                            "source_section": section,
                            "document_version": document.version,
                        },
                    )
                )
                row.embedding = values
                row.embedding_model, row.embedding_revision = identity.model, identity.revision
                row.embedding_dimensions, row.embedding_preprocessing = (
                    identity.dimension,
                    identity.preprocessing,
                )
                row.embedding_artifacts_sha256, row.pipeline_identity = (
                    identity.artifacts_sha256,
                    job.pipeline_identity,
                )
                row.embedded_at = now
                session.add(row)
            job.status, job.completed_at, job.chunk_count = "succeeded", now, len(prepared)
            job.lock_owner, job.locked_at, job.last_error = None, None, None
            await session.flush()
        return True

    async def fail_ingestion(
        self, claimed, owner, reason, *, permanent=False, quarantine=False, now=None
    ):
        now = now or datetime.now(UTC)
        if not isinstance(reason, str) or not re.fullmatch("[a-z][a-z0-9_]{0,63}", reason):
            reason = "ingestion_failed"
        async with self.session_factory() as session, session.begin():
            job = await self._owned(session, claimed, owner, now)
            if job is None:
                return "lost_lease"
            job.status = (
                "dead_letter"
                if permanent or quarantine or job.attempts >= MAX_ATTEMPTS
                else "queued"
            )
            job.quarantined, job.last_error = quarantine, reason
            job.available_at = now + timedelta(seconds=min(300, 2**job.attempts))
            job.lock_owner, job.locked_at = None, None
            return job.status

    async def _owned(self, session, claimed, owner, now):
        return await session.scalar(
            select(KnowledgeIngestionJob)
            .where(
                KnowledgeIngestionJob.id == claimed.id,
                KnowledgeIngestionJob.status == "processing",
                KnowledgeIngestionJob.lock_owner == owner,
                KnowledgeIngestionJob.attempts == claimed.attempts,
                KnowledgeIngestionJob.locked_at > now - timedelta(seconds=LEASE_SECONDS),
            )
            .with_for_update()
        )
