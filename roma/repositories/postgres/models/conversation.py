"""Durable latest-checkpoint metadata; live checkpoint integration is Level 2/7."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class ConversationState(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "conversation_states"
    __table_args__ = (
        UniqueConstraint("call_id"),
        CheckConstraint("schema_version > 0", name="schema_version_positive"),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("btrim(policy_version) <> ''", name="policy_version_nonblank"),
        CheckConstraint(
            "conversation_stage IN ('open', 'discover', 'value', 'structure', "
            "'pivot', 'objection', 'close')",
            name="conversation_stage",
        ),
        CheckConstraint("jsonb_typeof(state) = 'object'", name="state_object"),
        CheckConstraint("retention_until > created_at", name="retention_after_creation"),
        Index(None, "retention_until"),
    )

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    schema_version: Mapped[int] = mapped_column(server_default=text("1"))
    revision: Mapped[int] = mapped_column(server_default=text("1"))
    policy_version: Mapped[str] = mapped_column(String(128))
    conversation_stage: Mapped[str] = mapped_column(String(64))
    state: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # No implicit retention duration: writers must supply an approved expiry.
    retention_until: Mapped[datetime]
