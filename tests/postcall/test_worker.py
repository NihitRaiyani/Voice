"""The post-call worker (docs/09, docs/08).

The failure modes here are the ones that decide whether a recording is ever lost, so each
gets its own named test: consent-blocked, killed-before-durable-write, redelivery after a
crash, and a poison job that must not loop forever.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roma.postcall.job import OUTCOME_LOCKED, PostcallJob
from roma.postcall.queue import InMemoryPostcallQueue
from roma.postcall.spool import JobSpool
from roma.postcall.store import LocalRecordingStore
from roma.postcall.worker import JobResult, WorkerDeps, handle_job, run_worker

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _job(call_sid="CA_w", **kw) -> PostcallJob:
    base = dict(
        call_sid=call_sid,
        recording_ref=f"2026-07-26/{call_sid}.s16le",
        locked_slot="2026-07-27T15:00:00+05:30",
        started_at="2026-07-26T10:00:00+00:00",
        ended_at="2026-07-26T10:05:38.212000+00:00",
        duration_secs=338.2,
        audio_secs=337.9,
        outcome=OUTCOME_LOCKED,
    )
    base.update(kw)
    return PostcallJob(**base)


def _deps(tmp_path, *, consent=True, retention=90, spool=None, store=None):
    media = tmp_path / "media"
    return WorkerDeps(
        queue=InMemoryPostcallQueue(),
        store=store or LocalRecordingStore(tmp_path / "recordings"),
        media_root=media,
        consent_ok=lambda: consent,
        retention_days=retention,
        spool=spool,
        now_fn=lambda: NOW,
    )


def _capture(deps, job, frames=400):
    path = Path(deps.media_root) / job.recording_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x01\x02\x03\x04" * frames)
    return path


def test_placeholder_consent_refuses_to_store_and_deletes_the_capture(tmp_path, caplog):
    """CLAUDE.md gate 4 + docs/07 fail-safe.

    While CONSENT_LINE is the placeholder, no lead has been told they were recorded, so
    the audio must not be kept. The raw capture is DELETED rather than merely left
    unstored: unconsented lead audio sitting in `media/` forever, covered by no retention
    sweep, would itself be an unconsented recording.
    """
    deps = _deps(tmp_path, consent=False)
    job = _job()
    raw = _capture(deps, job)

    with caplog.at_level("ERROR"):
        result = asyncio.run(handle_job(job, deps))

    assert result is JobResult.ACK
    assert not raw.exists(), "unconsented capture must not be left on disk"
    assert not (tmp_path / "recordings").exists(), "nothing may be stored"
    assert "consent" in caplog.text.lower()


def test_signed_off_consent_stores_the_recording(tmp_path):
    """The same job, the same path — only the gate differs. This is what makes the whole
    flow exercisable today, while the real wording is still pending."""
    deps = _deps(tmp_path, consent=True)
    job = _job()
    raw = _capture(deps, job)

    assert asyncio.run(handle_job(job, deps)) is JobResult.ACK
    assert deps.store.already_stored(job)
    assert not raw.exists(), "the capture is expendable once the recording is durable"


def test_the_real_consent_predicate_currently_blocks():
    """A live wire, not a mock: while the placeholder ships, storage is off. This test
    flips to the other branch the day Weltec signs the wording off, which is the point."""
    from roma.dialer import CONSENT_LINE, consent_signed_off

    assert consent_signed_off(CONSENT_LINE) is False
    assert consent_signed_off("Namaste, ye call record ho rahi hai.") is True


class _FailingStore:
    """Fails the way a real crash does: AFTER the temp file, BEFORE the rename."""

    def __init__(self, root):
        self.root = Path(root)
        self.calls = 0

    def already_stored(self, job):
        return False

    async def put(self, job, raw_path):
        self.calls += 1
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{job.call_sid}.wav.tmp").write_bytes(b"partial")
        raise OSError("disk full")


def test_a_job_is_not_acked_until_the_recording_is_durable(tmp_path):
    """docs/09's core rule. If `put` raises, the job must go back on the queue — never be
    acked — and no final artifact may exist."""
    store = _FailingStore(tmp_path / "recordings")
    deps = _deps(tmp_path, store=store)
    job = _job()
    raw = _capture(deps, job)

    assert asyncio.run(handle_job(job, deps)) is JobResult.RETRY
    assert raw.exists(), "the capture must survive for the retry"
    assert not (store.root / "CA_w.wav").exists(), "no half-written artifact may be final"


def test_a_crash_between_store_and_ack_is_idempotent_on_redelivery(tmp_path):
    """The real crash window: killed after `os.replace`, before `LREM`. The job comes back,
    and must be recognised as done rather than reprocessed or errored."""
    deps = _deps(tmp_path)
    job = _job()
    _capture(deps, job)
    asyncio.run(handle_job(job, deps))
    assert deps.store.already_stored(job)

    assert asyncio.run(handle_job(job, deps)) is JobResult.ACK


def test_a_missing_capture_is_acked_not_looped_forever(tmp_path, caplog):
    """A poison job — nothing to store and nothing stored. It must leave the queue, or it
    blocks every subsequent job on every restart."""
    deps = _deps(tmp_path)
    with caplog.at_level("ERROR"):
        result = asyncio.run(handle_job(_job(), deps))

    assert result is JobResult.ACK
    assert "missing" in caplog.text.lower()


def test_repeated_failures_dead_letter_rather_than_retry_forever(tmp_path):
    from roma.postcall.worker import MAX_ATTEMPTS

    deps = _deps(tmp_path, store=_FailingStore(tmp_path / "recordings"))
    job = _job(attempts=MAX_ATTEMPTS - 1)
    _capture(deps, job)

    assert asyncio.run(handle_job(job, deps)) is JobResult.DEAD


def test_worker_processes_a_queued_job_end_to_end(tmp_path):
    deps = _deps(tmp_path)
    job = _job()
    _capture(deps, job)

    async def run():
        await deps.queue.push(job)
        return await run_worker(deps, max_jobs=1)

    stats = asyncio.run(run())
    assert stats.processed == 1 and stats.stored == 1
    assert deps.store.already_stored(job)
    assert deps.queue.inflight_depth() == 0, "a completed job must not stay inflight"


def test_worker_drains_the_spool_before_polling(tmp_path):
    """A job spooled while Redis was down must be picked up without a worker RESTART —
    draining only at startup would strand every job spooled afterwards."""
    spool = JobSpool(tmp_path / "spool")
    deps = _deps(tmp_path, spool=spool)
    job = _job()
    _capture(deps, job)
    spool.write(job)

    stats = asyncio.run(run_worker(deps, max_jobs=1))
    assert stats.stored == 1
    assert spool.pending() == []
    assert deps.store.already_stored(job)


def test_worker_runs_the_whole_path_with_no_redis(tmp_path):
    """Today's actual environment: Redis is not running. The spool-backed queue must carry
    reserve/ack, not just the fallback write."""
    from roma.postcall.queue import SpoolPostcallQueue

    spool = JobSpool(tmp_path / "spool")
    deps = _deps(tmp_path)
    deps.queue = SpoolPostcallQueue(spool)
    job = _job()
    _capture(deps, job)

    async def run():
        await deps.queue.push(job)
        return await run_worker(deps, max_jobs=1)

    stats = asyncio.run(run())
    assert stats.stored == 1
    assert deps.store.already_stored(job)
    assert spool.pending() == [], "the job must be acked off disk"


def test_drain_mode_exits_when_the_queue_is_empty(tmp_path):
    """What a deploy script calls before taking the box down (docs/08 §drain)."""
    deps = _deps(tmp_path)

    async def run():
        return await asyncio.wait_for(run_worker(deps, drain=True), timeout=5)

    stats = asyncio.run(run())
    assert stats.processed == 0


def test_worker_finishes_the_current_job_after_stop_is_set(tmp_path):
    """`stop` is checked only at the top of the loop, deliberately: abandoning a job
    mid-write is exactly the crash window the inflight list exists to cover."""
    deps = _deps(tmp_path)
    job = _job()
    _capture(deps, job)

    async def run():
        stop = asyncio.Event()
        await deps.queue.push(job)
        stats = await run_worker(deps, stop=stop, max_jobs=1)
        stop.set()
        return stats

    stats = asyncio.run(run())
    assert stats.stored == 1


def test_worker_recovers_jobs_left_inflight_by_a_previous_run(tmp_path):
    deps = _deps(tmp_path)
    job = _job()
    _capture(deps, job)

    async def run():
        await deps.queue.push(job)
        await deps.queue.reserve()
        assert deps.queue.inflight_depth() == 1
        return await run_worker(deps, max_jobs=1)

    stats = asyncio.run(run())
    assert stats.stored == 1
    assert deps.store.already_stored(job)


def test_worker_prunes_expired_days_at_startup(tmp_path):
    deps = _deps(tmp_path, retention=30)
    old = deps.store.root / (NOW - timedelta(days=100)).strftime("%Y-%m-%d")
    old.mkdir(parents=True)
    (old / "CA_old.wav").write_bytes(b"\x00\x00")

    stats = asyncio.run(run_worker(deps, drain=True))
    assert stats.pruned_days == 1
    assert not old.exists()


def test_a_retention_failure_does_not_take_the_worker_down(tmp_path):
    class _Boom:
        root = "/definitely/not/a/path/that/exists"

        def already_stored(self, job):
            return False

    deps = _deps(tmp_path)
    deps.store = _Boom()
    stats = asyncio.run(run_worker(deps, drain=True))
    assert stats.pruned_days == 0


@pytest.mark.parametrize("consent", [True, False])
def test_the_job_always_leaves_the_queue(tmp_path, consent):
    """Whichever way the gate falls, the queue must not grow without bound."""
    deps = _deps(tmp_path, consent=consent)
    job = _job()
    _capture(deps, job)

    async def run():
        await deps.queue.push(job)
        await run_worker(deps, max_jobs=1)
        return deps.queue.depth(), deps.queue.inflight_depth()

    assert asyncio.run(run()) == (0, 0)
