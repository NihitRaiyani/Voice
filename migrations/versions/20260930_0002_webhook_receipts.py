"""Durable webhook receipts independent of call retention.

Revision ID: 20260930_0002
Revises: 20260920_0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260930_0002"
down_revision = "20260920_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_receipts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_receipts")),
        sa.UniqueConstraint(
            "provider_event_id", name=op.f("uq_webhook_receipts_provider_event_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("webhook_receipts")
