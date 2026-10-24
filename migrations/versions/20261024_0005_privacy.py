"""Selected field encryption storage and durable privacy disposal tombstones.

Revision ID: 20261024_0005
Revises: 20261023_0004

No key generation or data encryption/backfill occurs in this migration. Operators
activate a separately managed key explicitly; legacy plaintext remains readable.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261024_0005"
down_revision = "20261023_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("callers", "name", type_=sa.Text(), existing_type=sa.String(255))
    op.add_column("recordings", sa.Column("disclosure_version", sa.String(128)))
    op.create_table(
        "privacy_tombstones",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("provider_call_id", sa.String(255), nullable=False),
        sa.Column("call_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column(
            "cleanup_pending", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_privacy_tombstones")),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["calls.id"],
            ondelete="RESTRICT",
            name=op.f("fk_privacy_tombstones_call_id_calls"),
        ),
        sa.UniqueConstraint(
            "provider_call_id", name=op.f("uq_privacy_tombstones_provider_call_id")
        ),
        sa.UniqueConstraint("call_id", name=op.f("uq_privacy_tombstones_call_id")),
    )


def downgrade():
    op.drop_table("privacy_tombstones")
    op.drop_column("recordings", "disclosure_version")
    # Ciphertexts can exceed 255 characters. Preserve TEXT on rollback to avoid
    # truncation/data loss; disabling encryption does not decrypt stored data.
