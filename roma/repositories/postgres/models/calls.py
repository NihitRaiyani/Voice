"""Caller identity and finalized call history."""

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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Caller(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "callers"

    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(255))
    preferred_language: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(128))
    education: Mapped[str | None] = mapped_column(Text)
    current_status: Mapped[str | None] = mapped_column(String(128))
    anonymized_at: Mapped[datetime | None]
    retention_until: Mapped[datetime | None]


class Call(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint("direction IN ('inbound', 'outbound')", name="direction"),
        CheckConstraint(
            "status IN ('pending', 'ringing', 'in_progress', 'completed', 'failed', 'cancelled', 'no_answer', 'busy')",
            name="status",
        ),
        CheckConstraint("total_cost >= 0", name="total_cost_nonnegative"),
        CheckConstraint("char_length(currency) = 3", name="currency_length"),
        Index(None, "caller_id", "created_at"),
        Index(None, "status", "created_at"),
        Index(None, "started_at"),
    )

    caller_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("callers.id", ondelete="SET NULL")
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    direction: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime]
    answered_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    final_stage: Mapped[str | None] = mapped_column(String(64))
    booking_status: Mapped[str | None] = mapped_column(String(32))
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    recording_url: Mapped[str | None] = mapped_column(Text)
    retention_until: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class CallTurn(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "call_turns"
    __table_args__ = (
        UniqueConstraint("call_id", "turn_number"),
        CheckConstraint("turn_number > 0", name="turn_number_positive"),
        CheckConstraint("speaker IN ('caller', 'agent', 'system')", name="speaker"),
        CheckConstraint("latency_ms >= 0", name="latency_ms_nonnegative"),
        Index(None, "call_id", "created_at"),
    )

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    turn_number: Mapped[int]
    speaker: Mapped[str] = mapped_column(String(16))
    transcript: Mapped[str | None] = mapped_column(Text)
    conversation_stage: Mapped[str | None] = mapped_column(String(64))
    route: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None]
    retention_until: Mapped[datetime | None]
    transcript_deleted_at: Mapped[datetime | None]


class CallEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "call_events"
    __table_args__ = (Index(None, "call_id", "occurred_at"),)

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(128))
    conversation_stage: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
