"""Short, locked governance transactions and a fail-closed eligibility query."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import or_, select, text

from roma.domain.knowledge import governance
from roma.repositories.postgres.knowledge_ingestion import identity_fields
from roma.repositories.postgres.models.calls import CallTurn
from roma.repositories.postgres.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestionJob,
    RetrievalLog,
)
from roma.schemas.knowledge import ChunkMetadata, RetrievalMetadata


class KnowledgeRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def create(self, request, *, actor_id, ingestion_required=False, ingestion=None):
        governance.validate_content(request.content)
        governance.validate_content(request.title)
        ingestion_values = None
        if ingestion is not None:
            pipeline = ingestion["pipeline_identity"]
            now = ingestion.get("now") or datetime.now(UTC)
            if (
                not isinstance(pipeline, str)
                or not 1 <= len(pipeline) <= 128
                or now.tzinfo is None
            ):
                raise governance.KnowledgeError()
            ingestion_values = identity_fields(ingestion["identity"]) | {
                "pipeline_identity": pipeline,
                "available_at": now,
            }
            ingestion_required = True
        document = KnowledgeDocument(
            id=uuid4(),
            uploaded_by=actor_id,
            status="draft",
            ingestion_required=ingestion_required,
            **request.model_dump(),
        )
        document.content_hash = governance.content_hash(document)
        async with self.session_factory() as session, session.begin():
            session.add(document)
            if ingestion_values is not None:
                # The original and its durable intent commit together. Redis delivery
                # may be retried independently without losing the upload after a crash.
                await session.flush()
                session.add(
                    KnowledgeIngestionJob(
                        id=uuid4(),
                        document_id=document.id,
                        expected_hash=document.content_hash,
                        **ingestion_values,
                    )
                )
            await session.flush()
        return document

    async def get(self, document_id):
        async with self.session_factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
            if document is None:
                raise governance.KnowledgeError("KNOWLEDGE_NOT_FOUND", 404)
            return document

    async def transition(
        self, document_id, action, *, actor_id, now, reference=None, expected_version=None
    ):
        async with self.session_factory() as session, session.begin():
            source_key = await session.scalar(
                select(KnowledgeDocument.source_key).where(KnowledgeDocument.id == document_id)
            )
            if source_key is None:
                raise governance.KnowledgeError("KNOWLEDGE_NOT_FOUND", 404)
            # Serialize this source family before row locks, including concurrent versions.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:source_key))"),
                {"source_key": source_key},
            )
            document = await session.scalar(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.id == document_id)
                .with_for_update()
            )
            if document is None:
                raise governance.KnowledgeError("KNOWLEDGE_NOT_FOUND", 404)
            if expected_version is not None and document.version != expected_version:
                raise governance.KnowledgeError("KNOWLEDGE_VERSION_CONFLICT", 409)
            if action == "approve":
                governance.approve(document, actor_id=actor_id, reference=reference, now=now)
            elif action == "activate":
                if document.ingestion_required:
                    ready = await session.scalar(
                        select(KnowledgeIngestionJob.id)
                        .where(
                            KnowledgeIngestionJob.document_id == document.id,
                            KnowledgeIngestionJob.expected_hash == document.content_hash,
                            KnowledgeIngestionJob.status == "succeeded",
                            KnowledgeIngestionJob.chunk_count > 0,
                        )
                        .limit(1)
                    )
                    if ready is None:
                        raise governance.KnowledgeError("KNOWLEDGE_INGESTION_REQUIRED", 409)
                # Validate first so an invalid replacement cannot retire current evidence.
                governance.activate(document, now=now)
                with session.no_autoflush:
                    previous = (
                        await session.scalars(
                            select(KnowledgeDocument)
                            .where(
                                KnowledgeDocument.source_key == document.source_key,
                                KnowledgeDocument.status == "active",
                                KnowledgeDocument.id != document.id,
                            )
                            .with_for_update()
                        )
                    ).all()
                # Autoflush is disabled while looking up old evidence; persist retirements
                # before the unique active-family index admits the replacement.
                for old in previous:
                    governance.archive(old, now=now)
                if previous:
                    document.status, document.activated_at = "approved", None
                    await session.flush()
                    governance.activate(document, now=now)
            elif action == "archive":
                governance.archive(document, now=now)
            else:
                raise governance.KnowledgeError()
            await session.flush()
        return document

    async def eligible_documents(
        self, *, now, category, language, course_id=None, branch_id=None, limit=20
    ):
        if not 1 <= limit <= 100 or now.tzinfo is None:
            raise governance.KnowledgeError()
        stmt = (
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.status == "active",
                KnowledgeDocument.category == category,
                KnowledgeDocument.language == language,
                KnowledgeDocument.approved_by.is_not(None),
                KnowledgeDocument.approved_at.is_not(None),
                KnowledgeDocument.approval_reference.is_not(None),
                KnowledgeDocument.approved_hash == KnowledgeDocument.content_hash,
                KnowledgeDocument.effective_from <= now,
                or_(
                    KnowledgeDocument.effective_until.is_(None),
                    KnowledgeDocument.effective_until > now,
                ),
                or_(
                    KnowledgeDocument.course_id.is_(None),
                    KnowledgeDocument.course_id == course_id,
                ),
                or_(
                    KnowledgeDocument.branch_id.is_(None),
                    KnowledgeDocument.branch_id == branch_id,
                ),
            )
            .order_by(KnowledgeDocument.source_key, KnowledgeDocument.version.desc())
            .limit(limit)
        )
        async with self.session_factory() as session:
            documents = (await session.scalars(stmt)).all()
        # Recompute the approved payload hash as defense against unreviewed storage edits.
        return [
            document
            for document in documents
            if governance.eligible(
                document, now=now, language=language, course_id=course_id, branch_id=branch_id
            )
        ]

    async def record_retrieval(self, metadata: RetrievalMetadata, *, now=None):
        # No raw query/transcript API. IDs and scores must refer to governed evidence.
        metadata = RetrievalMetadata.model_validate(metadata.model_dump())
        now = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            if metadata.turn_id is not None:
                linked_call = await session.scalar(
                    select(CallTurn.call_id).where(CallTurn.id == metadata.turn_id)
                )
                if linked_call != metadata.call_id:
                    raise governance.KnowledgeError("KNOWLEDGE_EVIDENCE_MISMATCH")
            if metadata.chunk_ids:
                chunks = (
                    await session.scalars(
                        select(KnowledgeChunk).where(KnowledgeChunk.id.in_(metadata.chunk_ids))
                    )
                ).all()
                if (
                    len(chunks) != len(metadata.chunk_ids)
                    or metadata.selected_document_id is None
                ):
                    raise governance.KnowledgeError("KNOWLEDGE_EVIDENCE_MISMATCH")
                document = await session.get(KnowledgeDocument, metadata.selected_document_id)
                if (
                    document is None
                    or document.category != metadata.category
                    or not governance.eligible(
                        document,
                        now=now,
                        language=document.language,
                        course_id=document.course_id,
                        branch_id=document.branch_id,
                    )
                ):
                    raise governance.KnowledgeError("KNOWLEDGE_EVIDENCE_MISMATCH")
                for chunk in chunks:
                    provenance = ChunkMetadata.model_validate(chunk.metadata_json)
                    if (
                        chunk.document_id != document.id
                        or chunk.language != document.language
                        or provenance.document_version != document.version
                        or chunk.content_hash != sha256(chunk.content.encode()).hexdigest()
                    ):
                        raise governance.KnowledgeError("KNOWLEDGE_EVIDENCE_MISMATCH")
                    governance.validate_content(chunk.content)
            session.add(RetrievalLog(id=uuid4(), **metadata.model_dump()))
