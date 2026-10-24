"""Offline handler policy; no provider calls or external messages."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from roma.repositories.postgres.background_jobs import ClaimedJob
from roma.workers.background import BackgroundHandlers, process_background_job


def _job(kind="send_followup", **payload):
    return ClaimedJob(uuid4(), uuid4(), kind, payload, 1, "stable-followup-key")


def test_followup_requires_consent_and_an_explicit_sender():
    async def run():
        sender = AsyncMock()
        sender.send.return_value = True
        store = AsyncMock()
        for handlers, payload, reason in (
            (
                BackgroundHandlers(),
                {"consent_confirmed": True},
                "followup_adapter_not_configured",
            ),
            (BackgroundHandlers(followup=sender), {}, "followup_consent_required"),
        ):
            job = _job(**payload)
            store.claim.return_value = job
            await process_background_job(store, job.id, handlers=handlers)
            assert store.fail.call_args.kwargs["error_type"] == reason
            assert store.fail.call_args.kwargs["permanent"] is True
        sender.send.assert_not_awaited()
        store.complete.assert_not_awaited()

    asyncio.run(run())


def test_followup_requires_positive_receipt_and_stable_provider_idempotency_key():
    async def run():
        sender = AsyncMock()
        store = AsyncMock()
        job = _job(consent_confirmed=True)
        store.claim.return_value = job
        handlers = BackgroundHandlers(followup=sender)
        sender.send.return_value = False
        await process_background_job(store, job.id, handlers=handlers)
        store.complete.assert_not_awaited()
        assert store.fail.call_args.kwargs["error_type"] == "FollowupNotConfirmed"
        sender.send.return_value = True
        await process_background_job(store, job.id, handlers=handlers)
        sender.send.assert_awaited_with(
            call_id=job.call_id, idempotency_key=job.idempotency_key
        )
        assert store.complete.call_args.kwargs["event_type"] == "followup_sent"
        assert store.complete.call_args.kwargs["event_payload"] == {"adapter_confirmed": True}

    asyncio.run(run())


def test_summary_adapter_is_used_only_by_summary_jobs():
    async def run():
        summary = AsyncMock()
        summary.summarize.return_value = {
            "text": "Synthetic metadata summary.",
            "source": "claude_call_metadata",
        }
        handlers = BackgroundHandlers(summary=summary)
        job = _job("summarize_call", final_stage="close", outcome="no_lock")
        effect = await handlers.effect_for(job)
        assert effect.event_type == "postcall_summary"
        assert effect.payload["source"] == "claude_call_metadata"
        summary.summarize.assert_awaited_once_with(job.payload)

    asyncio.run(run())
