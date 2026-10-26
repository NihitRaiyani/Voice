"""Exact cosine retrieval with SQL scopes and approved-payload rechecks."""

import math
from hashlib import sha256

from sqlalchemy import Float, String, bindparam, cast, exists, or_, select

from roma.domain.knowledge import governance
from roma.domain.knowledge.ingestion import chunk_document, matches_canonical_chunk
from roma.domain.knowledge.retrieval import Evidence
from roma.repositories.postgres.knowledge_ingestion import identity_fields
from roma.repositories.postgres.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestionJob,
)
from roma.repositories.postgres.vector import Vector, vector_literal, vector_values
from roma.schemas.knowledge import ChunkMetadata


class KnowledgeSearchRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def _statement(self, identity, filters, now):
        fields = identity_fields(identity)
        if (
            now.tzinfo is None
            or filters.category not in {"course", "policy", "branch", "schedule"}
            or filters.language not in {"en", "hi", "gu", "hi-en"}
        ):
            raise governance.KnowledgeError()
        ready = exists(
            select(KnowledgeIngestionJob.id).where(
                KnowledgeIngestionJob.document_id == KnowledgeDocument.id,
                KnowledgeIngestionJob.expected_hash == KnowledgeDocument.content_hash,
                KnowledgeIngestionJob.pipeline_identity == KnowledgeChunk.pipeline_identity,
                KnowledgeIngestionJob.status == "succeeded",
                KnowledgeIngestionJob.model == fields["model"],
                KnowledgeIngestionJob.revision == fields["revision"],
                KnowledgeIngestionJob.dimensions == fields["dimensions"],
                KnowledgeIngestionJob.preprocessing == fields["preprocessing"],
                KnowledgeIngestionJob.artifacts_sha256 == fields["artifacts_sha256"],
            )
        )
        stmt = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.status == "active",
                KnowledgeDocument.category == filters.category,
                KnowledgeDocument.language.in_(filters.languages),
                KnowledgeChunk.language == KnowledgeDocument.language,
                KnowledgeDocument.approved_by.is_not(None),
                KnowledgeDocument.approved_at <= now,
                KnowledgeDocument.approval_reference.is_not(None),
                KnowledgeDocument.approved_hash == KnowledgeDocument.content_hash,
                KnowledgeDocument.effective_from <= now,
                or_(
                    KnowledgeDocument.effective_until.is_(None),
                    KnowledgeDocument.effective_until > now,
                ),
                or_(
                    KnowledgeDocument.course_id.is_(None),
                    KnowledgeDocument.course_id == filters.course_id,
                ),
                or_(
                    KnowledgeDocument.branch_id.is_(None),
                    KnowledgeDocument.branch_id == filters.branch_id,
                ),
                KnowledgeChunk.embedding.is_not(None),
                KnowledgeChunk.embedding_model == fields["model"],
                KnowledgeChunk.embedding_revision == fields["revision"],
                KnowledgeChunk.embedding_dimensions == fields["dimensions"],
                KnowledgeChunk.embedding_preprocessing == fields["preprocessing"],
                KnowledgeChunk.embedding_artifacts_sha256 == fields["artifacts_sha256"],
                ready,
            )
        )
        if filters.version is not None:
            stmt = stmt.where(KnowledgeDocument.version == filters.version)
        return stmt

    def _evidence(self, chunk, document, filters, now, score, canonical_cache=None):
        if not governance.eligible(
            document,
            now=now,
            language=document.language,
            course_id=filters.course_id,
            branch_id=filters.branch_id,
        ):
            return None
        try:
            provenance = ChunkMetadata.model_validate(chunk.metadata_json)
            governance.validate_content(chunk.content)
            vector_values(chunk.embedding)
            if canonical_cache is None:
                canonical = chunk_document(document.content, language=document.language)
            else:
                if document.id not in canonical_cache:
                    canonical_cache[document.id] = chunk_document(
                        document.content, language=document.language
                    )
                canonical = canonical_cache[document.id]
            if not matches_canonical_chunk(
                canonical,
                ordinal=chunk.ordinal,
                content=chunk.content,
                content_hash=chunk.content_hash,
                language=chunk.language,
                source_section=provenance.source_section,
            ):
                return None
        except (ValueError, TypeError):
            return None
        if (
            provenance.document_version != document.version
            or chunk.content_hash != sha256(chunk.content.encode()).hexdigest()
            or not math.isfinite(score)
        ):
            return None
        return Evidence(
            chunk_id=chunk.id,
            document_id=document.id,
            content=chunk.content,
            content_hash=chunk.content_hash,
            document_version=document.version,
            language=chunk.language,
            score=max(-1.0, min(1.0, score)),
            source_section=provenance.source_section,
        )

    async def search_chunks(self, query_vector, identity, filters, now, k, threshold):
        values = vector_values(query_vector)
        if (
            len(values) != identity.dimension
            or type(k) is not int
            or not 1 <= k <= 20
            or not math.isfinite(threshold)
            or not -1 <= threshold <= 1
        ):
            raise governance.KnowledgeError("KNOWLEDGE_SEARCH_INVALID")
        # No approximate index exists. All governance/model/scope predicates restrict
        # eligible rows before exact ordering. Threshold is applied after scoring.
        distance = KnowledgeChunk.embedding.op("<=>", return_type=Float)(
            cast(bindparam("query_vector", type_=String), Vector())
        )
        stmt = (
            self._statement(identity, filters, now)
            .add_columns((1 - distance).label("score"))
            .order_by(distance, KnowledgeChunk.id)
            .limit(k)
        )
        async with self.session_factory() as session:
            rows = (await session.execute(stmt, {"query_vector": vector_literal(values)})).all()
        evidence, canonical_cache = [], {}
        for chunk, document, score in rows:
            item = self._evidence(chunk, document, filters, now, float(score), canonical_cache)
            if item is not None and item.score >= threshold:
                evidence.append(item)
        return tuple(evidence)

    async def revalidate_evidence(self, chunk_ids, filters, identity, now):
        if not chunk_ids:
            return ()
        if len(chunk_ids) > 20 or len(set(chunk_ids)) != len(chunk_ids):
            raise governance.KnowledgeError("KNOWLEDGE_SEARCH_INVALID")
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    self._statement(identity, filters, now).where(
                        KnowledgeChunk.id.in_(chunk_ids)
                    )
                )
            ).all()
        found, canonical_cache = {}, {}
        for chunk, document in rows:
            evidence = self._evidence(chunk, document, filters, now, 0.0, canonical_cache)
            if evidence is not None:
                found[chunk.id] = evidence
        return tuple(found[chunk_id] for chunk_id in chunk_ids if chunk_id in found)
