"""Add conversation checkpoints and model/benchmark evidence without replacing legacy tables.

Revision ID: 20261004_0003
Revises: 20260930_0002
Create Date: 2026-10-04 08:28:26.733793+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261004_0003"
down_revision: str | None = "20260930_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add new foundations; historical turn language remains unknown (NULL)."""
    op.create_table(
        "benchmark_runs",
        sa.Column("run_key", sa.String(length=128), nullable=False),
        sa.Column("dataset_name", sa.String(length=255), nullable=False),
        sa.Column("dataset_version", sa.String(length=128), nullable=False),
        sa.Column("dataset_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("sample_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "environment",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (ended_at IS NOT NULL)",
            name=op.f("ck_benchmark_runs_terminal_has_end"),
        ),
        sa.CheckConstraint(
            "btrim(run_key) <> '' AND btrim(dataset_name) <> '' AND btrim(dataset_version) <> ''",
            name=op.f("ck_benchmark_runs_identity_nonblank"),
        ),
        sa.CheckConstraint(
            "dataset_sha256 IS NULL OR dataset_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_benchmark_runs_dataset_sha256"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'", name=op.f("ck_benchmark_runs_config_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(environment) = 'object'",
            name=op.f("ck_benchmark_runs_environment_object"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR sample_count > 0",
            name=op.f("ck_benchmark_runs_succeeded_has_samples"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_benchmark_runs_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('running', 'succeeded') OR started_at IS NOT NULL",
            name=op.f("ck_benchmark_runs_started_when_executing"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_benchmark_runs_time_order"),
        ),
        sa.CheckConstraint(
            "retention_until IS NULL OR retention_until > created_at",
            name=op.f("ck_benchmark_runs_retention_after_creation"),
        ),
        sa.CheckConstraint(
            "sample_count >= 0", name=op.f("ck_benchmark_runs_sample_count_nonnegative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_runs")),
        sa.UniqueConstraint("run_key", name=op.f("uq_benchmark_runs_run_key")),
    )
    op.create_index(
        op.f("ix_benchmark_runs_dataset_name_dataset_version_created_at"),
        "benchmark_runs",
        ["dataset_name", "dataset_version", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_benchmark_runs_retention_until"),
        "benchmark_runs",
        ["retention_until"],
        unique=False,
        postgresql_where=sa.text("retention_until IS NOT NULL"),
    )
    op.create_index(
        op.f("ix_benchmark_runs_status_created_at"),
        "benchmark_runs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_table(
        "model_registry",
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("license_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint(
            "btrim(provider) <> '' AND btrim(name) <> '' AND btrim(version) <> ''",
            name=op.f("ck_model_registry_identity_nonblank"),
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_model_registry_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "task IN ('stt', 'llm', 'tts', 'embedding', 'reranker', 'vad')",
            name=op.f("ck_model_registry_task"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_registry")),
        sa.UniqueConstraint(
            "provider",
            "name",
            "version",
            "task",
            name=op.f("uq_model_registry_provider_name_version_task"),
        ),
    )
    op.create_index(
        op.f("ix_model_registry_task_is_active"),
        "model_registry",
        ["task", "is_active"],
        unique=False,
    )
    op.create_table(
        "benchmark_results",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(language) <> '' AND btrim(metric) <> '' AND btrim(unit) <> ''",
            name=op.f("ck_benchmark_results_measurement_nonblank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name=op.f("ck_benchmark_results_metadata_object"),
        ),
        sa.CheckConstraint(
            "value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name=op.f("ck_benchmark_results_value_finite"),
        ),
        sa.CheckConstraint(
            "sample_count > 0", name=op.f("ck_benchmark_results_sample_count_positive")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["model_registry.id"],
            name=op.f("fk_benchmark_results_model_id_model_registry"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["benchmark_runs.id"],
            name=op.f("fk_benchmark_results_run_id_benchmark_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_results")),
        sa.UniqueConstraint(
            "run_id",
            "model_id",
            "language",
            "metric",
            name=op.f("uq_benchmark_results_run_id_model_id_language_metric"),
        ),
    )
    op.create_index(
        op.f("ix_benchmark_results_model_id_language_metric"),
        "benchmark_results",
        ["model_id", "language", "metric"],
        unique=False,
    )
    op.create_table(
        "conversation_states",
        sa.Column("call_id", sa.UUID(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("conversation_stage", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint(
            "btrim(policy_version) <> ''",
            name=op.f("ck_conversation_states_policy_version_nonblank"),
        ),
        sa.CheckConstraint(
            "conversation_stage IN ('open', 'discover', 'value', 'structure', 'pivot', 'objection', 'close')",
            name=op.f("ck_conversation_states_conversation_stage"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(state) = 'object'", name=op.f("ck_conversation_states_state_object")
        ),
        sa.CheckConstraint(
            "retention_until > created_at",
            name=op.f("ck_conversation_states_retention_after_creation"),
        ),
        sa.CheckConstraint(
            "revision > 0", name=op.f("ck_conversation_states_revision_positive")
        ),
        sa.CheckConstraint(
            "schema_version > 0", name=op.f("ck_conversation_states_schema_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["calls.id"],
            name=op.f("fk_conversation_states_call_id_calls"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_states")),
        sa.UniqueConstraint("call_id", name=op.f("uq_conversation_states_call_id")),
    )
    op.create_index(
        op.f("ix_conversation_states_retention_until"),
        "conversation_states",
        ["retention_until"],
        unique=False,
    )
    op.add_column("call_turns", sa.Column("language", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Remove only this increment; its new evidence/language data will be lost."""
    op.drop_column("call_turns", "language")
    op.drop_index(
        op.f("ix_conversation_states_retention_until"), table_name="conversation_states"
    )
    op.drop_table("conversation_states")
    op.drop_index(
        op.f("ix_benchmark_results_model_id_language_metric"), table_name="benchmark_results"
    )
    op.drop_table("benchmark_results")
    op.drop_index(op.f("ix_model_registry_task_is_active"), table_name="model_registry")
    op.drop_table("model_registry")
    op.drop_index(op.f("ix_benchmark_runs_status_created_at"), table_name="benchmark_runs")
    op.drop_index(
        op.f("ix_benchmark_runs_retention_until"),
        table_name="benchmark_runs",
        postgresql_where=sa.text("retention_until IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_benchmark_runs_dataset_name_dataset_version_created_at"),
        table_name="benchmark_runs",
    )
    op.drop_table("benchmark_runs")
