"""Separate consumer for durable, non-realtime post-call jobs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

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


async def run_background_worker(
    store: BackgroundJobStore,
    *,
    stop: asyncio.Event | None = None,
    max_jobs: int | None = None,
    drain: bool = False,
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
            effect = effect_for(job)
            settled = await asyncio.wait_for(
                store.complete(
                    job,
                    owner=owner,
                    now=datetime.now(UTC),
                    event_type=effect.event_type,
                    event_payload=effect.payload,
                    caller_status=effect.caller_status,
                ),
                timeout=JOB_TIMEOUT_SECS,
            )
            if not settled:
                _log.warning("background job lost lease id=%s", job.id)
        except Exception as exc:  # noqa: BLE001 — retry with sanitized error type
            try:
                status = await store.fail(
                    job,
                    owner=owner,
                    now=datetime.now(UTC),
                    error_type=type(exc).__name__,
                )
                _log.error("background job %s %s after %s", job.id, status, type(exc).__name__)
            except Exception as settle_exc:  # noqa: BLE001 — lease expiry recovers this claim
                _log.error("background settle failed (%s)", type(settle_exc).__name__)
    return processed
