"""Model identity and reproducible benchmark evidence, independent of call PII."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class ModelRegistry(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "model_registry"
    __table_args__ = (
        UniqueConstraint("provider", "name", "version", "task"),
        CheckConstraint(
            "btrim(provider) <> '' AND btrim(name) <> '' AND btrim(version) <> ''",
            name="identity_nonblank",
        ),
        CheckConstraint(
            "task IN ('stt', 'llm', 'tts', 'embedding', 'reranker', 'vad')", name="task"
        ),
        CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="checksum_sha256",
        ),
        Index(None, "task", "is_active"),
    )

    provider: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(128))
    task: Mapped[str] = mapped_column(String(32))
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    license_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(server_default=true())


class BenchmarkRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (
        UniqueConstraint("run_key"),
        CheckConstraint(
            "btrim(run_key) <> '' AND btrim(dataset_name) <> '' "
            "AND btrim(dataset_version) <> ''",
            name="identity_nonblank",
        ),
        CheckConstraint(
            "dataset_sha256 IS NULL OR dataset_sha256 ~ '^[0-9a-f]{64}$'",
            name="dataset_sha256",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("sample_count >= 0", name="sample_count_nonnegative"),
        CheckConstraint(
            "status <> 'succeeded' OR sample_count > 0", name="succeeded_has_samples"
        ),
        CheckConstraint(
            "status NOT IN ('running', 'succeeded') OR started_at IS NOT NULL",
            name="started_when_executing",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (ended_at IS NOT NULL)",
            name="terminal_has_end",
        ),
        CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="time_order",
        ),
        CheckConstraint("jsonb_typeof(config) = 'object'", name="config_object"),
        CheckConstraint("jsonb_typeof(environment) = 'object'", name="environment_object"),
        CheckConstraint(
            "retention_until IS NULL OR retention_until > created_at",
            name="retention_after_creation",
        ),
        Index(None, "status", "created_at"),
        Index(None, "dataset_name", "dataset_version", "created_at"),
        Index(None, "retention_until", postgresql_where=text("retention_until IS NOT NULL")),
    )

    run_key: Mapped[str] = mapped_column(String(128))
    dataset_name: Mapped[str] = mapped_column(String(255))
    dataset_version: Mapped[str] = mapped_column(String(128))
    dataset_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), server_default=text("'pending'"))
    sample_count: Mapped[int] = mapped_column(server_default=text("0"))
    config: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    environment: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    retention_until: Mapped[datetime | None]


class BenchmarkResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (
        UniqueConstraint("run_id", "model_id", "language", "metric"),
        CheckConstraint(
            "btrim(language) <> '' AND btrim(metric) <> '' AND btrim(unit) <> ''",
            name="measurement_nonblank",
        ),
        CheckConstraint(
            "value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="value_finite",
        ),
        CheckConstraint("sample_count > 0", name="sample_count_positive"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        Index(None, "model_id", "language", "metric"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("benchmark_runs.id", ondelete="CASCADE"))
    model_id: Mapped[UUID] = mapped_column(ForeignKey("model_registry.id", ondelete="RESTRICT"))
    language: Mapped[str] = mapped_column(String(32))
    metric: Mapped[str] = mapped_column(String(64))
    # Signed metrics are allowed; per-metric ranges belong to the benchmark runner.
    value: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    unit: Mapped[str] = mapped_column(String(32))
    sample_count: Mapped[int]
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
