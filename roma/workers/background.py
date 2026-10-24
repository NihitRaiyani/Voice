"""Separate consumer for durable, non-realtime post-call jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from roma.repositories.postgres.background_jobs import BackgroundJobStore, ClaimedJob

_log = logging.getLogger("roma.workers.background")
JOB_TIMEOUT_SECS = 60


@dataclass(frozen=True)
class JobEffect:
    event_type: str
    payload: dict[str, object]
    caller_status: str | None = None


def effect_for(job: ClaimedJob) -> JobEffect:
    """Only derived facts go into events; queued payloads contain no transcript or PII."""
    facts = job.payload
    if job.job_type == "compute_statistics":
        duration = max(0.0, float(facts["duration_secs"]))
        audio = max(0.0, float(facts["audio_secs"]))
        return JobEffect(
            "postcall_statistics",
            {
                "duration_secs": duration,
                "audio_secs": audio,
                "turn_count": max(0, int(facts["turn_count"])),
                "speech_fraction": round(min(audio / duration, 1.0), 4) if duration else 0.0,
            },
        )
    if job.job_type == "summarize_call":
        stage = str(facts.get("final_stage") or "unknown")
        outcome = str(facts["outcome"])
        return JobEffect(
            "postcall_summary",
            {
                "text": f"Call ended at {stage}; visit time captured: {outcome == 'locked'}.",
                "source": "deterministic_call_metadata",
            },
        )
    if job.job_type == "update_lead":
        status = "visit_time_captured" if facts["outcome"] == "locked" else "contacted"
        return JobEffect("lead_status_updated", {"status": status}, status)
    raise ValueError(f"unsupported job type: {job.job_type}")


class PermanentJobFailure(RuntimeError):
    """Reviewed operational reason; no provider error bodies are retained."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class FollowupNotConfirmed(RuntimeError):
    pass


class FollowupSender(Protocol):
    async def send(self, *, call_id: UUID, idempotency_key: str) -> bool:
        """Honor provider idempotency; return True only for confirmed delivery."""
        ...


class SummaryGenerator(Protocol):
    async def summarize(self, facts: Mapping[str, object]) -> dict[str, object]: ...


class BackgroundHandlers:
    """Explicit adapters only; an injected sender must honor provider idempotency.

    A sender resolves the approved recipient and message from durable call facts.
    It accepts the stable idempotency key and returns exactly True after a confirmed
    send. This cannot make an arbitrary non-idempotent external provider exactly-once.
    """

    def __init__(
        self, *, summary: SummaryGenerator | None = None, followup: FollowupSender | None = None
    ) -> None:
        self.summary, self.followup = summary, followup

    async def effect_for(self, job: ClaimedJob) -> JobEffect:
        if job.job_type == "send_followup":
            if job.payload.get("consent_confirmed") is not True:
                raise PermanentJobFailure("followup_consent_required")
            if self.followup is None:
                raise PermanentJobFailure("followup_adapter_not_configured")
            confirmed = await self.followup.send(
                call_id=job.call_id, idempotency_key=job.idempotency_key
            )
            if confirmed is not True:
                raise FollowupNotConfirmed("followup_not_confirmed")
            return JobEffect("followup_sent", {"adapter_confirmed": True})
        if job.job_type == "summarize_call" and self.summary is not None:
            return JobEffect("postcall_summary", await self.summary.summarize(job.payload))
        if job.job_type == "process_recording":
            # The consent-gated recording consumer must finish conversion before the
            # SQL handoff. Do not manufacture a second conversion or a success here.
            raise PermanentJobFailure("recording_worker_required")
        return effect_for(job)


async def execute_claimed_job(
    store, job: ClaimedJob, *, owner: str, handlers: BackgroundHandlers | None = None
) -> bool:
    """Share the same business effect/transaction boundary across queue consumers."""
    async with asyncio.timeout(JOB_TIMEOUT_SECS):
        effect = await (handlers or BackgroundHandlers()).effect_for(job)
        return await store.complete(
            job,
            owner=owner,
            now=datetime.now(UTC),
            event_type=effect.event_type,
            event_payload=effect.payload,
            caller_status=effect.caller_status,
        )


async def process_background_job(
    store, job_id: UUID, *, handlers: BackgroundHandlers | None = None
) -> None:
    """Handle one framework delivery. PostgreSQL owns business retries and status."""
    owner = str(uuid4())
    job = await store.claim(now=datetime.now(UTC), owner=owner, job_id=job_id)
    if job is None:
        return  # Completed, not due, already owned, deleted, or unknown delivery.
    try:
        await execute_claimed_job(store, job, owner=owner, handlers=handlers)
    except Exception as exc:  # noqa: BLE001 — persist retry policy without sensitive details
        await store.fail(
            job,
            owner=owner,
            now=datetime.now(UTC),
            error_type=exc.reason
            if isinstance(exc, PermanentJobFailure)
            else type(exc).__name__,
            permanent=isinstance(exc, PermanentJobFailure),
        )


async def run_background_worker(
    store: BackgroundJobStore,
    *,
    stop: asyncio.Event | None = None,
    max_jobs: int | None = None,
    drain: bool = False,
    handlers: BackgroundHandlers | None = None,
) -> int:
    """Claim and settle jobs. A crash leaves a lease for another process to reclaim."""
    owner = str(uuid4())
    processed = 0
    while stop is None or not stop.is_set():
        if max_jobs is not None and processed >= max_jobs:
            break
        try:
            job = await store.claim(now=datetime.now(UTC), owner=owner)
        except Exception as exc:  # noqa: BLE001 — database outage should not kill the process
            _log.error("background claim failed (%s)", type(exc).__name__)
            if drain:
                break
            await asyncio.sleep(5)
            continue
        if job is None:
            if drain:
                break
            await asyncio.sleep(1)
            continue
        processed += 1
        try:
            settled = await execute_claimed_job(store, job, owner=owner, handlers=handlers)
            if not settled:
                _log.warning("background job lost lease id=%s", job.id)
        except Exception as exc:  # noqa: BLE001 — retry with sanitized error type
            try:
                status = await store.fail(
                    job,
                    owner=owner,
                    now=datetime.now(UTC),
                    error_type=exc.reason
                    if isinstance(exc, PermanentJobFailure)
                    else type(exc).__name__,
                    permanent=isinstance(exc, PermanentJobFailure),
                )
                _log.error("background job %s %s after %s", job.id, status, type(exc).__name__)
            except Exception as settle_exc:  # noqa: BLE001 — lease expiry recovers this claim
                _log.error("background settle failed (%s)", type(settle_exc).__name__)
    return processed
