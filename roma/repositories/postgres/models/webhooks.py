"""Minimal durable receipts; kept independently of business-record deletion."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class WebhookReceipt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "webhook_receipts"

    provider_event_id: Mapped[str] = mapped_column(String(255), unique=True)
    payload_digest: Mapped[str] = mapped_column(String(64))
