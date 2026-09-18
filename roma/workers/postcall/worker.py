"""The post-call worker: drain the queue, store recordings, enforce consent and retention.

docs/08: "slow work lives here, never in the pipeline." The worker is a separate process
consuming `queue:postcall`; nothing in this module may import pipecat.

The ORDER inside `handle_job` is the contract, not an implementation detail — see the
docstring there. And a job is acked only after `store.put` returns, which is the whole of
docs/09's "a killed worker must never silently drop a recording".
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from roma.postcall.job import PostcallJob
from roma.postcall.store import prune

_log = logging.getLogger("roma.postcall")

POLL_TIMEOUT_SECS = 5
MAX_ATTEMPTS = 5
RETENTION_SWEEP_SECS = 6 * 3600
QUEUE_ERROR_BACKOFF_SECS = 5.0


class JobResult(Enum):
    ACK = "ack"
    RETRY = "retry"
    DEAD = "dead"


@dataclass
class WorkerStats:
    processed: int = 0
    stored: int = 0
    blocked_by_consent: int = 0
    retried: int = 0
    dead_lettered: int = 0
    pruned_days: int = 0
    queue_errors: int = 0


@dataclass
class WorkerDeps:
    queue: object
    store: object
    media_root: Path
    consent_ok: Callable[[], bool]
    retention_days: int
    spool: object = None
    now_fn: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


async def handle_job(job: PostcallJob, deps: WorkerDeps) -> JobResult:
    """Process one job. The step order is load-bearing; do not reorder casually.

    1. **Consent gate, first.** If the disclosure wording is still the placeholder, no lead
       has actually been told they were recorded, so we must not keep the audio — and the
       raw capture is DELETED, not merely left unstored. Leaving unconsented lead audio
       sitting in `media/` forever, where no retention sweep covers it, would itself be
       storing an unconsented recording. The job is ACKed: there is nothing to retry, the
       policy will not change on its own, and requeuing forever would just churn.
    2. **Idempotent redelivery.** Already stored and the raw file gone means a previous
       run got killed in the window between `os.replace` and the ack. The work is done;
       ack and move on. Without this explicit case the job loops forever.
    3. **Poison guard.** No raw file and nothing stored: unrecoverable. Log loudly and ACK
       — an unprocessable job must never become an infinite retry loop.
    4. **Store, then ack.** `store.put` returns only once both the WAV and the sidecar are
       fsynced and renamed.
    """
    raw_path = Path(deps.media_root) / job.recording_ref

    if not deps.consent_ok():
        _log.error(
            "postcall: storage BLOCKED — consent wording not signed off (CLAUDE.md gate 4); "
            "discarding capture for call_sid=%s",
            job.call_sid,
        )
        raw_path.unlink(missing_ok=True)
        return JobResult.ACK

    if deps.store.already_stored(job):
        _log.info("postcall: already stored, acking redelivery call_sid=%s", job.call_sid)
        raw_path.unlink(missing_ok=True)
        return JobResult.ACK

    if not raw_path.exists():
        _log.error(
            "postcall: capture file missing for call_sid=%s (ref=%s); nothing to store",
            job.call_sid,
            job.recording_ref,
        )
        return JobResult.ACK

    try:
        await deps.store.put(job, raw_path)
    except Exception as exc:  # noqa: BLE001 — decide retry vs dead-letter, never crash
        if job.attempts + 1 >= MAX_ATTEMPTS:
            _log.error(
                "postcall: giving up on call_sid=%s after %d attempts (%s)",
                job.call_sid,
                job.attempts + 1,
                type(exc).__name__,
            )
            return JobResult.DEAD
        _log.warning(
            "postcall: store failed for call_sid=%s (%s); will retry",
            job.call_sid,
            type(exc).__name__,
        )
        return JobResult.RETRY

    raw_path.unlink(missing_ok=True)
    return JobResult.ACK


async def run_worker(
    deps: WorkerDeps,
    *,
    stop: "asyncio.Event | None" = None,
    max_jobs: "int | None" = None,
    drain: bool = False,
) -> WorkerStats:
    """Consume the queue until stopped (or, in `drain` mode, until it is empty.)

    `max_jobs` is the test seam — run exactly N jobs and return, with no race against an
    event. `stop` is the production seam, set by SIGINT/SIGTERM.

    `stop` is only checked at the TOP of the loop, so a job already in flight runs to
    completion rather than being abandoned mid-write. That is deliberate: the work is
    bounded (one file conversion) and abandoning it is precisely the crash window the
    inflight list exists to cover.
    """
    stats = WorkerStats()
    stats.pruned_days = _sweep_retention(deps)
    last_sweep = deps.now_fn()

    recovered = await deps.queue.recover_inflight()
    if recovered:
        _log.info("postcall: %d job(s) recovered from a previous run", recovered)

    idle_polls = 0
    while not (stop is not None and stop.is_set()):
        if max_jobs is not None and stats.processed >= max_jobs:
            break

        try:
            if deps.spool is not None:
                await deps.spool.drain_into(deps.queue)

            job = await deps.queue.reserve(POLL_TIMEOUT_SECS)
        except Exception as exc:  # noqa: BLE001 — a Redis blip must not take the worker down
            _log.error("postcall: queue unreachable (%s); backing off", type(exc).__name__)
            stats.queue_errors += 1
            idle_polls += 1
            if drain and idle_polls >= 2:
                break
            await asyncio.sleep(QUEUE_ERROR_BACKOFF_SECS)
            continue
        if job is None:
            idle_polls += 1
            if drain and idle_polls >= 2:
                break
            if (deps.now_fn() - last_sweep).total_seconds() >= RETENTION_SWEEP_SECS:
                stats.pruned_days += _sweep_retention(deps)
                last_sweep = deps.now_fn()
            continue

        idle_polls = 0
        stats.processed += 1
        consent_before = deps.consent_ok()
        result = await handle_job(job, deps)

        try:
            if result is JobResult.ACK:
                await deps.queue.ack(job)
                if consent_before:
                    stats.stored += 1
                else:
                    stats.blocked_by_consent += 1
            elif result is JobResult.RETRY:
                await deps.queue.retry(job)
                stats.retried += 1
            else:
                await deps.queue.dead(job)
                stats.dead_lettered += 1
        except Exception as exc:  # noqa: BLE001 — an unsettled job stays inflight; recoverable
            # The job remains on the inflight list, which `recover_inflight` re-queues at the
            # next startup — the same crash window the list exists for. Losing the whole
            # worker to a settle failure would strand every job BEHIND this one too.
            _log.error(
                "postcall: could not settle call_sid=%s (%s); it stays in flight",
                job.call_sid,
                type(exc).__name__,
            )
            stats.queue_errors += 1
            await asyncio.sleep(QUEUE_ERROR_BACKOFF_SECS)

    return stats


def _sweep_retention(deps: WorkerDeps) -> int:
    try:
        return prune(deps.store.root, deps.retention_days, deps.now_fn())
    except Exception as exc:  # noqa: BLE001 — retention must not take the worker down
        _log.error("postcall: retention sweep failed (%s)", type(exc).__name__)
        return 0


__all__ = [
    "WorkerDeps",
    "WorkerStats",
    "JobResult",
    "handle_job",
    "run_worker",
    "MAX_ATTEMPTS",
    "POLL_TIMEOUT_SECS",
    "QUEUE_ERROR_BACKOFF_SECS",
    "RETENTION_SWEEP_SECS",
]
