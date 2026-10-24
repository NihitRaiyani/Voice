"""Add governed knowledge metadata only; no embeddings, extensions or seed facts.

Revision ID: 20261024_0006
Revises: 20261024_0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "20261024_0006"
down_revision = "20261024_0005"
branch_labels = None
depends_on = None


def _base():
    return [
        sa.Column(
            "id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def _reference(name, table, *, nullable=False):
    return sa.Column(
        name, sa.UUID(), sa.ForeignKey(table + ".id", ondelete="RESTRICT"), nullable=nullable
    )


def upgrade():
    op.create_table(
        "knowledge_documents",
        *_base(),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("source_reference", sa.String(128), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        _reference("owner_id", "users"),
        _reference("uploaded_by", "users"),
        _reference("course_id", "courses", nullable=True),
        _reference("branch_id", "branches", nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        _reference("approved_by", "users", nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approval_reference", sa.String(128)),
        sa.Column("approved_hash", sa.String(64)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_key", "version"),
        sa.CheckConstraint("status IN ('draft','approved','active','archived')", name="status"),
        sa.CheckConstraint(
            "category IN ('course','policy','branch','schedule')", name="category"
        ),
        sa.CheckConstraint("language IN ('hi','en','gu','hi-en')", name="language"),
        sa.CheckConstraint("version > 0", name="version"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="effective_dates",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approval_reference IS NOT NULL AND approved_hash IS NOT NULL AND approved_hash = content_hash)",
            name="approved_provenance",
        ),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 20000", name="content_bound"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="hash_shape"),
        sa.CheckConstraint(
            "status NOT IN ('active','archived') OR activated_at IS NOT NULL", name="activation"
        ),
        sa.CheckConstraint("status <> 'archived' OR archived_at IS NOT NULL", name="archive"),
    )
    op.create_index(
        "uq_knowledge_documents_active_source",
        "knowledge_documents",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_knowledge_documents_eligibility",
        "knowledge_documents",
        ["status", "language", "category", "effective_from"],
    )
    op.create_table(
        "knowledge_chunks",
        *_base(),
        _reference("document_id", "knowledge_documents"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(128)),
        sa.Column("embedding_revision", sa.String(128)),
        sa.Column("embedding_dimensions", sa.Integer()),
        sa.Column("embedded_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("document_id", "ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ordinal"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 2000", name="content_bound"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="hash_shape"),
        sa.CheckConstraint(
            "embedding_dimensions IS NULL OR embedding_dimensions > 0",
            name="embedding_dimensions",
        ),
        sa.CheckConstraint("language IN ('hi','en','gu','hi-en')", name="language"),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object' AND octet_length(metadata_json::text) <= 1024",
            name="bounded_metadata",
        ),
    )
    op.create_table(
        "retrieval_logs",
        *_base(),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("call_id", sa.UUID(), sa.ForeignKey("calls.id", ondelete="SET NULL")),
        sa.Column("turn_id", sa.UUID(), sa.ForeignKey("call_turns.id", ondelete="SET NULL")),
        sa.Column("chunk_ids", ARRAY(sa.UUID()), nullable=False, server_default="{}"),
        sa.Column("scores", ARRAY(sa.Float()), nullable=False, server_default="{}"),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("route", sa.String(16), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        _reference("selected_document_id", "knowledge_documents", nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("route IN ('knowledge','no_evidence')", name="route"),
        sa.CheckConstraint(
            "category IN ('course','policy','branch','schedule')", name="category"
        ),
        sa.CheckConstraint(
            "outcome IN ('eligible','no_evidence','rejected','unavailable')", name="outcome"
        ),
        sa.CheckConstraint(
            "eligible_count BETWEEN 0 AND 100 AND latency_ms BETWEEN 0 AND 60000",
            name="bounded_metrics",
        ),
        sa.CheckConstraint(
            "cardinality(chunk_ids) = cardinality(scores) AND cardinality(scores) <= 20 AND 1 >= ALL(scores) AND -1 <= ALL(scores)",
            name="bounded_evidence",
        ),
    )
    # Approved payload/provenance is immutable even for direct storage edits. A new
    # version needs a new draft and its own owner approval; archive cannot be undone.
    op.execute("""
CREATE FUNCTION protect_knowledge_approval() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' AND
     ROW(NEW.source_key,NEW.source_reference,NEW.title,NEW.version,NEW.category,NEW.language,NEW.owner_id,NEW.uploaded_by,NEW.course_id,NEW.branch_id,NEW.effective_from,NEW.effective_until,NEW.content,NEW.content_hash,NEW.approved_by,NEW.approved_at,NEW.approval_reference,NEW.approved_hash)
     IS DISTINCT FROM
     ROW(OLD.source_key,OLD.source_reference,OLD.title,OLD.version,OLD.category,OLD.language,OLD.owner_id,OLD.uploaded_by,OLD.course_id,OLD.branch_id,OLD.effective_from,OLD.effective_until,OLD.content,OLD.content_hash,OLD.approved_by,OLD.approved_at,OLD.approval_reference,OLD.approved_hash)
  THEN RAISE EXCEPTION 'approved knowledge is immutable'; END IF;
  IF NEW.status <> OLD.status AND NOT (
     (OLD.status='draft' AND NEW.status='approved') OR
     (OLD.status='approved' AND NEW.status='active') OR
     (OLD.status='active' AND NEW.status='archived'))
  THEN RAISE EXCEPTION 'invalid knowledge lifecycle transition'; END IF;
  RETURN NEW;
END $$;
""")
    op.execute(
        "CREATE TRIGGER knowledge_approval_immutable BEFORE UPDATE ON knowledge_documents FOR EACH ROW EXECUTE FUNCTION protect_knowledge_approval()"
    )


def downgrade():
    op.execute("DROP TRIGGER knowledge_approval_immutable ON knowledge_documents")
    op.execute("DROP FUNCTION protect_knowledge_approval()")
    op.drop_table("retrieval_logs")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
