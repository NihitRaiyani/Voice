"""Branch-local appointment slots and committed bookings."""

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class AppointmentSlot(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "appointment_slots"
    __table_args__ = (
        UniqueConstraint("branch_id", "appointment_date", "start_time"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("end_time > start_time", name="time_order"),
        CheckConstraint(
            "status IN ('available', 'held', 'booked', 'blocked', 'cancelled')", name="status"
        ),
        Index(None, "branch_id", "appointment_date", "status", "start_time"),
    )

    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    appointment_date: Mapped[date]
    start_time: Mapped[time]
    end_time: Mapped[time]
    capacity: Mapped[int]
    status: Mapped[str] = mapped_column(String(32))


class Appointment(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["branch_id", "appointment_date", "start_time"],
            [
                "appointment_slots.branch_id",
                "appointment_slots.appointment_date",
                "appointment_slots.start_time",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('booked', 'confirmed', 'completed', 'cancelled', 'no_show')",
            name="status",
        ),
        Index(
            "uq_appointments_active_slot",
            "branch_id",
            "appointment_date",
            "start_time",
            unique=True,
            postgresql_where=text("status IN ('booked', 'confirmed')"),
        ),
        Index(None, "caller_id", "appointment_date"),
        Index(None, "branch_id", "appointment_date", "status"),
    )

    caller_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("callers.id", ondelete="SET NULL")
    )
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    branch_id: Mapped[UUID]
    appointment_date: Mapped[date]
    start_time: Mapped[time]
    counsellor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("counsellors.id", ondelete="SET NULL")
    )
    course_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32))
    retention_until: Mapped[datetime | None]
    anonymized_at: Mapped[datetime | None]
