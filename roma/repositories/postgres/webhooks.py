"""Receipt uniqueness and business effects share one PostgreSQL transaction."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roma.domain.webhooks import AnswerEvent, WebhookConflict, WebhookUnavailable

from .models import Call, CallEvent, WebhookReceipt


class WebhookRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def accept_answer(self, event: AnswerEvent) -> bool:
        try:
            async with self._session_factory() as session, session.begin():
                receipt_id = await session.scalar(
                    insert(WebhookReceipt)
                    .values(
                        provider_event_id=event.provider_event_id,
                        payload_digest=event.payload_digest,
                    )
                    .on_conflict_do_nothing(index_elements=[WebhookReceipt.provider_event_id])
                    .returning(WebhookReceipt.id)
                )
                if receipt_id is None:
                    # The unique index waits for a competing transaction. At READ
                    # COMMITTED this next statement sees its committed receipt.
                    digest = await session.scalar(
                        select(WebhookReceipt.payload_digest).where(
                            WebhookReceipt.provider_event_id == event.provider_event_id
                        )
                    )
                    if digest != event.payload_digest:
                        raise WebhookConflict("event identity reused with different input")
                    return False

                now = datetime.now(UTC)
                call_id = await session.scalar(
                    insert(Call)
                    .values(
                        provider_call_id=event.provider_call_id,
                        direction=event.direction,
                        status="in_progress",
                        started_at=now,
                        answered_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=[Call.provider_call_id])
                    .returning(Call.id)
                )
                if call_id is None:
                    # A delayed answer must never overwrite a finalized call.
                    call_id = await session.scalar(
                        select(Call.id).where(Call.provider_call_id == event.provider_call_id)
                    )
                session.add(
                    CallEvent(
                        call_id=call_id,
                        event_type="twilio.answer",
                        idempotency_key=event.provider_event_id,
                        payload={},
                        occurred_at=now,
                    )
                )
            return True
        except (SQLAlchemyError, OSError) as exc:
            # Do not expose SQL parameters, credentials, or raw provider input.
            raise WebhookUnavailable("webhook persistence unavailable") from exc
