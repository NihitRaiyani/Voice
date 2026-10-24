"""Governed evidence metadata; embeddings remain nullable future metadata only."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class KnowledgeDocument(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("source_key", "version"),
        Index(
            "uq_knowledge_documents_active_source",
            "source_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint("status IN ('draft','approved','active','archived')", name="status"),
        CheckConstraint("category IN ('course','policy','branch','schedule')", name="category"),
        CheckConstraint("language IN ('hi','en','gu','hi-en')", name="language"),
        CheckConstraint("version > 0", name="version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="effective_dates",
        ),
        CheckConstraint(
            "status = 'draft' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approval_reference IS NOT NULL AND approved_hash IS NOT NULL AND approved_hash = content_hash)",
            name="approved_provenance",
        ),
        CheckConstraint("length(content) BETWEEN 1 AND 20000", name="content_bound"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="hash_shape"),
        CheckConstraint(
            "status NOT IN ('active','archived') OR activated_at IS NOT NULL", name="activation"
        ),
        CheckConstraint("status <> 'archived' OR archived_at IS NOT NULL", name="archive"),
        Index(
            "ix_knowledge_documents_eligibility",
            "status",
            "language",
            "category",
            "effective_from",
        ),
    )
    source_key: Mapped[str] = mapped_column(String(128))
    source_reference: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(160))
    version: Mapped[int]
    category: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(8))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    course_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT")
    )
    branch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT")
    )
    effective_from: Mapped[datetime]
    effective_until: Mapped[datetime | None]
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None]
    approval_reference: Mapped[str | None] = mapped_column(String(128))
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    activated_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]


class KnowledgeChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal"),
        CheckConstraint("length(content) BETWEEN 1 AND 2000", name="content_bound"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="hash_shape"),
        CheckConstraint(
            "embedding_dimensions IS NULL OR embedding_dimensions > 0",
            name="embedding_dimensions",
        ),
        CheckConstraint("language IN ('hi','en','gu','hi-en')", name="language"),
        CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object' AND octet_length(metadata_json::text) <= 1024",
            name="bounded_metadata",
        ),
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="RESTRICT")
    )
    ordinal: Mapped[int]
    language: Mapped[str] = mapped_column(String(8))
    metadata_json: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_revision: Mapped[str | None] = mapped_column(String(128))
    embedding_dimensions: Mapped[int | None]
    embedded_at: Mapped[datetime | None]


class RetrievalLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "retrieval_logs"
    __table_args__ = (
        CheckConstraint("route IN ('knowledge','no_evidence')", name="route"),
        CheckConstraint("category IN ('course','policy','branch','schedule')", name="category"),
        CheckConstraint(
            "outcome IN ('eligible','no_evidence','rejected','unavailable')", name="outcome"
        ),
        CheckConstraint(
            "eligible_count BETWEEN 0 AND 100 AND latency_ms BETWEEN 0 AND 60000",
            name="bounded_metrics",
        ),
        CheckConstraint(
            "cardinality(chunk_ids) = cardinality(scores) AND cardinality(scores) <= 20 AND 1 >= ALL(scores) AND -1 <= ALL(scores)",
            name="bounded_evidence",
        ),
    )
    request_id: Mapped[UUID]
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    turn_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("call_turns.id", ondelete="SET NULL")
    )
    chunk_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), server_default="{}"
    )
    scores: Mapped[list[float]] = mapped_column(ARRAY(Float), server_default="{}")
    fallback_used: Mapped[bool] = mapped_column(Boolean)
    route: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16))
    eligible_count: Mapped[int]
    selected_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="RESTRICT")
    )
    latency_ms: Mapped[int]
