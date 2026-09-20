"""Operational evidence, metering, costs, recording references, jobs, and audit."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class SafetyEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "safety_events"
    __table_args__ = (Index(None, "call_id", "created_at"), Index(None, "rule", "created_at"))

    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    turn_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("call_turns.id", ondelete="SET NULL")
    )
    rule: Mapped[str | None] = mapped_column(String(128))
    original_category: Mapped[str | None] = mapped_column(String(128))
    replacement_type: Mapped[str | None] = mapped_column(String(128))
    # DeclarativeBase reserves `metadata`; the persisted/domain column remains `metadata`.
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    retention_until: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class ProviderUsage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "provider_usage"
    __table_args__ = (
        CheckConstraint("service IN ('telephony', 'stt', 'llm', 'tts')", name="service"),
        CheckConstraint("measured_units >= 0", name="measured_units_nonnegative"),
        Index(None, "call_id", "service", "occurred_at"),
        Index(None, "provider", "service", "occurred_at"),
    )

    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    turn_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("call_turns.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(128))
    service: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(255))
    measured_units: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    unit_name: Mapped[str] = mapped_column(String(64))
    provider_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    retention_until: Mapped[datetime | None]


class CallCost(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "call_costs"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint("char_length(currency) = 3", name="currency_length"),
        Index(None, "call_id", "occurred_at"),
        Index(None, "provider", "service", "occurred_at"),
    )

    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    provider_usage_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_usage.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(128))
    service: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    unit: Mapped[str] = mapped_column(String(64))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    currency: Mapped[str] = mapped_column(String(3))
    pricing_version: Mapped[str] = mapped_column(String(128))
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    retention_until: Mapped[datetime | None]


class Recording(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "recordings"
    __table_args__ = (
        UniqueConstraint("storage_provider", "object_key"),
        CheckConstraint("duration_ms >= 0", name="duration_ms_nonnegative"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'available', 'deleted', 'failed')", name="status"
        ),
        Index(None, "retention_until", "status"),
    )

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    storage_provider: Mapped[str] = mapped_column(String(128))
    object_key: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    consent_at: Mapped[datetime | None]
    retention_until: Mapped[datetime]
    deleted_at: Mapped[datetime | None]
    deletion_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )


class FollowupJob(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "followup_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'dead_letter')",
            name="status",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index(None, "status", "available_at"),
    )

    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    appointment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL")
    )
    job_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(32), server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(server_default=func.now())
    lock_owner: Mapped[str | None] = mapped_column(String(255))
    locked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    retention_until: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(None, "actor_user_id", "occurred_at"),
        Index(None, "resource_type", "resource_id", "occurred_at"),
    )

    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(128))
    resource_id: Mapped[UUID]
    request_id: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    retention_until: Mapped[datetime | None]
