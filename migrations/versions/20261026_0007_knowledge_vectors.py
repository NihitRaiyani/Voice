"""Add exact pgvector and durable document ingestion; no data/model activation.

Revision ID: 20261026_0007
Revises: 20261024_0006
"""

import sqlalchemy as sa
from alembic import op
from roma.repositories.postgres.vector import Vector

revision = "20261026_0007"
down_revision = "20261024_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "knowledge_documents",
        sa.Column("ingestion_required", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("knowledge_chunks", sa.Column("embedding", Vector()))
    op.add_column("knowledge_chunks", sa.Column("embedding_preprocessing", sa.String(128)))
    op.add_column("knowledge_chunks", sa.Column("embedding_artifacts_sha256", sa.String(64)))
    op.add_column("knowledge_chunks", sa.Column("pipeline_identity", sa.String(128)))
    op.create_check_constraint(
        "vector_identity",
        "knowledge_chunks",
        "embedding IS NULL OR (embedding_model IS NOT NULL AND embedding_revision IS NOT NULL AND embedding_dimensions IS NOT NULL AND embedded_at IS NOT NULL AND embedding_preprocessing IS NOT NULL AND embedding_artifacts_sha256 IS NOT NULL AND pipeline_identity IS NOT NULL AND vector_dims(embedding) = embedding_dimensions AND vector_norm(embedding) > 0)",
    )
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column(
            "id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "document_id",
            sa.UUID(),
            sa.ForeignKey("knowledge_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expected_hash", sa.String(64), nullable=False),
        sa.Column("pipeline_identity", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("revision", sa.String(128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("preprocessing", sa.String(128), nullable=False),
        sa.Column("artifacts_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lock_owner", sa.String(128)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("document_id", "expected_hash", "pipeline_identity"),
        sa.CheckConstraint(
            "status IN ('queued','processing','succeeded','dead_letter')", name="status"
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="attempts"),
        sa.CheckConstraint("expected_hash ~ '^[0-9a-f]{64}$'", name="expected_hash"),
        sa.CheckConstraint("dimensions BETWEEN 1 AND 16000", name="dimensions"),
        sa.CheckConstraint("chunk_count BETWEEN 0 AND 1000", name="chunk_count"),
        sa.CheckConstraint(
            "length(pipeline_identity) BETWEEN 1 AND 128", name="pipeline_identity"
        ),
        sa.CheckConstraint(
            "last_error IS NULL OR last_error ~ '^[a-z][a-z0-9_]{0,63}$'", name="safe_reason"
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_dispatch",
        "knowledge_ingestion_jobs",
        ["status", "available_at"],
    )
    op.execute("""
CREATE FUNCTION protect_knowledge_ingestion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.ingestion_required AND NOT NEW.ingestion_required
  THEN RAISE EXCEPTION 'managed ingestion cannot be bypassed'; END IF;
  IF NEW.status = 'active' AND OLD.status <> 'active' AND NEW.ingestion_required
     AND NOT EXISTS (
       SELECT 1 FROM knowledge_ingestion_jobs j
       WHERE j.document_id = NEW.id AND j.expected_hash = NEW.content_hash
         AND j.status = 'succeeded' AND j.chunk_count > 0
         AND j.chunk_count = (SELECT count(*) FROM knowledge_chunks c
           WHERE c.document_id=NEW.id AND c.pipeline_identity=j.pipeline_identity
             AND c.embedding IS NOT NULL AND c.embedding_model=j.model
             AND c.embedding_revision=j.revision AND c.embedding_dimensions=j.dimensions
             AND c.embedding_preprocessing=j.preprocessing
             AND c.embedding_artifacts_sha256=j.artifacts_sha256)
     )
  THEN RAISE EXCEPTION 'managed knowledge requires quality checked ingestion'; END IF;
  RETURN NEW;
END $$;
""")
    op.execute(
        "CREATE TRIGGER knowledge_ingestion_quality BEFORE UPDATE ON knowledge_documents FOR EACH ROW EXECUTE FUNCTION protect_knowledge_ingestion()"
    )


def downgrade():
    op.execute("DROP TRIGGER knowledge_ingestion_quality ON knowledge_documents")
    op.execute("DROP FUNCTION protect_knowledge_ingestion()")
    op.drop_table("knowledge_ingestion_jobs")
    op.drop_constraint(
        op.f("ck_knowledge_chunks_vector_identity"), "knowledge_chunks", type_="check"
    )
    for name in [
        "pipeline_identity",
        "embedding_artifacts_sha256",
        "embedding_preprocessing",
        "embedding",
    ]:
        op.drop_column("knowledge_chunks", name)
    op.drop_column("knowledge_documents", "ingestion_required")
    # The extension may serve unrelated schemas; never drop a shared extension.
