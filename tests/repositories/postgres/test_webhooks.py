"""Real PostgreSQL proves atomic deduplication, rollback, and concurrent delivery."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from alembic import command
from roma.domain.webhooks import AnswerEvent, WebhookConflict, WebhookUnavailable
from roma.repositories.postgres.models import Call, CallEvent, WebhookReceipt
from roma.repositories.postgres.webhooks import WebhookRepository
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url

__all__ = ["alembic_config", "disposable_postgres_url", "session_factory"]
pytestmark = pytest.mark.postgres


@pytest.fixture()
def session_factory(alembic_config, disposable_postgres_url):
    command.upgrade(alembic_config, "head")
    # Tests use asyncio.run per scenario. Do not retain pooled connections across loops.
    engine = create_async_engine(disposable_postgres_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())
    command.downgrade(alembic_config, "base")


def event():
    return AnswerEvent(
        provider_event_id="twilio:AC_test:CA_test:answer",
        provider_call_id="CA_test",
        direction="outbound",
        payload_digest="a" * 64,
    )


async def counts(factory):
    async with factory() as session:
        return tuple(
            [
                await session.scalar(select(func.count()).select_from(model))
                for model in (WebhookReceipt, Call, CallEvent)
            ]
        )


def test_concurrent_redelivery_commits_one_receipt_call_and_event(session_factory):
    async def run():
        # Independent repository instances model separate API workers.
        results = await asyncio.gather(
            *(WebhookRepository(session_factory).accept_answer(event()) for _ in range(20))
        )
        assert results.count(True) == 1
        assert results.count(False) == 19
        assert await counts(session_factory) == (1, 1, 1)
        assert await WebhookRepository(session_factory).accept_answer(event()) is False

    asyncio.run(run())


def test_failure_after_claim_rolls_back_and_delivery_can_retry(session_factory):
    async def run():
        repository = WebhookRepository(session_factory)
        # Break the final INSERT, after receipt and call creation have executed.
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "ALTER TABLE call_events ADD CONSTRAINT reject_answer_for_test "
                    "CHECK (event_type <> 'twilio.answer')"
                )
            )
        with pytest.raises(WebhookUnavailable):
            await repository.accept_answer(event())
        assert await counts(session_factory) == (0, 0, 0)
        async with session_factory() as session, session.begin():
            await session.execute(
                text("ALTER TABLE call_events DROP CONSTRAINT reject_answer_for_test")
            )
        assert await repository.accept_answer(event()) is True
        assert await counts(session_factory) == (1, 1, 1)

    asyncio.run(run())


def test_same_key_with_changed_effect_is_a_conflict(session_factory):
    async def run():
        repository = WebhookRepository(session_factory)
        await repository.accept_answer(event())
        with pytest.raises(WebhookConflict):
            await repository.accept_answer(replace(event(), payload_digest="b" * 64))
        assert await counts(session_factory) == (1, 1, 1)

    asyncio.run(run())


def test_answer_never_regresses_an_existing_completed_call(session_factory):
    async def run():
        async with session_factory() as session, session.begin():
            session.add(
                Call(
                    provider_call_id="CA_test",
                    direction="outbound",
                    status="completed",
                    started_at=datetime.now(UTC),
                    final_stage="close",
                )
            )
        assert await WebhookRepository(session_factory).accept_answer(event()) is True
        async with session_factory() as session:
            call = await session.scalar(select(Call))
            assert (call.status, call.final_stage) == ("completed", "close")
        assert await counts(session_factory) == (1, 1, 1)

    asyncio.run(run())


def test_receipt_survives_call_deletion_and_prevents_recreation(session_factory):
    async def run():
        repository = WebhookRepository(session_factory)
        await repository.accept_answer(event())
        async with session_factory() as session, session.begin():
            await session.execute(delete(Call))
        assert await repository.accept_answer(event()) is False
        assert await counts(session_factory) == (1, 0, 0)

    asyncio.run(run())


def test_distinct_calls_are_not_suppressed(session_factory):
    async def run():
        repository = WebhookRepository(session_factory)
        await repository.accept_answer(event())
        await repository.accept_answer(
            replace(
                event(),
                provider_event_id="twilio:AC_test:CA_other:answer",
                provider_call_id="CA_other",
            )
        )
        assert await counts(session_factory) == (2, 2, 2)

    asyncio.run(run())


def test_http_retries_and_process_restart_have_one_durable_effect(session_factory):
    from httpx import ASGITransport, AsyncClient
    from roma.services.webhook_service import WebhookService
    from tests.api.v1.test_webhook_idempotency import ACCOUNT, BASE, CALL, TOKEN, make_app
    from twilio.request_validator import RequestValidator

    async def run():
        params = {"AccountSid": ACCOUNT, "CallSid": CALL}
        signature = RequestValidator(TOKEN).compute_signature(BASE + "/answer", params)
        app, _ = make_app(WebhookService(WebhookRepository(session_factory)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/answer",
                        data=params,
                        headers={
                            "X-Twilio-Signature": signature,
                            "I-Twilio-Idempotency-Token": f"attempt-{i}",
                        },
                    )
                    for i in range(20)
                )
            )
        assert all(response.status_code == 200 for response in responses)
        assert (
            sum(response.headers["x-webhook-duplicate"] == "false" for response in responses)
            == 1
        )
        assert len({response.text for response in responses}) == 1
        # A new app/repository has no process-local memory of previous deliveries.
        app, _ = make_app(WebhookService(WebhookRepository(session_factory)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
            retry = await client.post(
                "/answer", data=params, headers={"X-Twilio-Signature": signature}
            )
        assert retry.status_code == 200
        assert retry.headers["x-webhook-duplicate"] == "true"
        assert await counts(session_factory) == (1, 1, 1)

    asyncio.run(run())


def test_postcall_worker_finalizes_the_existing_answer_record(session_factory):
    from roma.repositories.postgres.background_jobs import BackgroundJobStore
    from roma.services.postcall_service import PostcallService
    from tests.repositories.postgres.test_background_jobs import _message

    async def run():
        message = replace(_message(locked=True), call_sid="CA_test")
        await WebhookRepository(session_factory).accept_answer(event())
        await PostcallService(BackgroundJobStore(session_factory)).persist(message)
        async with session_factory() as session:
            call = await session.scalar(select(Call))
            assert call.status == "completed"
            assert call.final_stage == "close"
            assert call.started_at == datetime.fromisoformat(message.started_at)
            assert call.answered_at is not None
        assert await counts(session_factory) == (1, 1, 1)

    asyncio.run(run())
