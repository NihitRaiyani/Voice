"""Organization reference data and course offerings."""

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Institute(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "institutes"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(server_default=true())


class Branch(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("institute_id", "code"),
        Index(None, "institute_id", "is_active"),
    )

    institute_id: Mapped[UUID] = mapped_column(ForeignKey("institutes.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(server_default=true())


class Course(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("institute_id", "code"),
        Index(None, "institute_id", "is_active"),
    )

    institute_id: Mapped[UUID] = mapped_column(ForeignKey("institutes.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=true())


class BranchCourse(Base):
    __tablename__ = "branch_courses"

    branch_id: Mapped[UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), primary_key=True
    )
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"), primary_key=True
    )


class Counsellor(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "counsellors"
    __table_args__ = (
        UniqueConstraint("branch_id", "employee_code"),
        Index(None, "branch_id", "is_active"),
    )

    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    employee_code: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(server_default=true())
