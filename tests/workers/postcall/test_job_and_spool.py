"""The post-call job payload and the spool fallback (docs/09).

Async via `asyncio.run` (repo convention — there is no pytest-asyncio).
"""

import asyncio
import stat

import pytest

from roma.controller.state import CallState
from roma.postcall.job import (
    OUTCOME_LOCKED,
    OUTCOME_NO_LOCK,
    OUTCOME_UNKNOWN,
    PostcallJob,
    outcome_for,
)
from roma.postcall.paths import DIR_MODE, FILE_MODE
from roma.postcall.spool import JobSpool, SpoolFallbackQueue

EXPECTED_KEYS = {
    "v",
    "call_sid",
    "recording_ref",
    "locked_slot",
    "started_at",
    "ended_at",
    "duration_secs",
    "audio_secs",
    "outcome",
    "sample_rate",
    "num_channels",
    "attempts",
}


def _job(call_sid="CA_test", **kw) -> PostcallJob:
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


def test_round_trip_preserves_the_raw_string_byte_for_byte():
    """`raw` is what the Redis ack matches on. If it is ever re-derived rather than kept,
    `LREM` matches nothing, the entry leaks in `:inflight`, and the job is redelivered on
    every restart forever."""
    original = _job()
    raw = original.to_json()
    parsed = PostcallJob.from_raw(raw)

    assert parsed.raw == raw
    assert parsed == original


def test_serialisation_is_byte_stable():
    """Two equal jobs must serialise identically — the ack match depends on it."""
    assert _job().to_json() == _job().to_json()


def test_job_carries_exactly_the_expected_keys():
    assert set(_job().to_dict()) == EXPECTED_KEYS


def test_job_contains_no_pii():
    """docs/07: a lead's name, phone and transcript are PII. A queue payload is the last
    place they should end up — it is persisted, logged, and read by another process."""
    state = CallState(call_sid="CA_pii", phase="p7_close", lead_name="Rakesh")
    state.locked_slot = "2026-07-27T15:00:00+05:30"
    payload = _job(call_sid=state.call_sid, outcome=outcome_for(state)).to_json()

    assert "Rakesh" not in payload
    assert "phone" not in payload and "lead_name" not in payload


def test_sidecar_is_exactly_the_docs09_metadata_list():
    assert set(_job().sidecar()) == {
        "v",
        "call_sid",
        "timestamp",
        "duration",
        "outcome",
        "locked_slot",
    }


def test_with_attempt_increments_and_reserialises():
    j = _job()
    assert j.attempts == 0
    retried = j.with_attempt()
    assert retried.attempts == 1
    assert retried.raw is not None and retried.raw != j.raw


@pytest.mark.parametrize(
    "locked,expected",
    [("2026-07-27T15:00:00+05:30", OUTCOME_LOCKED), (None, OUTCOME_NO_LOCK)],
)
def test_outcome_for_reads_the_lock(locked, expected):
    """A soft "dekhta hoon" is `no_lock`, deliberately — CLAUDE.md: it is NOT a win."""
    state = CallState(call_sid="CA_o", phase="p7_close")
    state.locked_slot = locked
    assert outcome_for(state) == expected


def test_outcome_for_handles_no_state():
    assert outcome_for(None) == OUTCOME_UNKNOWN


def test_spool_write_is_atomic_and_owner_only(tmp_path):
    spool = JobSpool(tmp_path / "spool")
    path = spool.write(_job())

    assert path.exists()
    assert not list(path.parent.glob("*.tmp")), "a temp file was left behind"
    assert stat.S_IMODE(path.stat().st_mode) == FILE_MODE
    assert stat.S_IMODE(path.parent.stat().st_mode) == DIR_MODE
    assert PostcallJob.from_raw(path.read_text()) == _job()


def test_spool_filenames_sort_into_fifo_order(tmp_path):
    spool = JobSpool(tmp_path / "spool")
    for sid, ended in [
        ("CA_2", "2026-07-26T10:05:00.000000+00:00"),
        ("CA_1", "2026-07-26T10:00:00.000000+00:00"),
        ("CA_3", "2026-07-26T10:05:00.500000+00:00"),
    ]:
        spool.write(_job(sid, ended_at=ended))

    order = [spool.load(p).call_sid for p in spool.pending()]
    assert order == ["CA_1", "CA_2", "CA_3"]


def test_spool_filename_is_safe_and_carries_no_pii(tmp_path):
    """A Twilio SID and a timestamp are fine; a phone number or a name would not be. Keep
    the charset boring so the name is safe on any filesystem and in any log line."""
    spool = JobSpool(tmp_path / "spool")
    name = spool.write(_job()).name

    assert "CA_test" in name
    assert name.endswith(".json")
    assert all(ch.isalnum() or ch in "-_." for ch in name), name


class _FailingQueue:
    async def push(self, job):
        raise ConnectionError("redis down")


class _RecordingQueue:
    def __init__(self):
        self.pushed = []

    async def push(self, job):
        self.pushed.append(job)


def test_enqueue_failure_falls_back_to_the_spool(tmp_path):
    """Redis is not running locally, so this is the DEFAULT path today, not an edge case."""
    spool = JobSpool(tmp_path / "spool")
    q = SpoolFallbackQueue(_FailingQueue(), spool)

    assert asyncio.run(q.push(_job())) == "spooled"
    pending = spool.pending()
    assert len(pending) == 1
    assert spool.load(pending[0]).call_sid == "CA_test"


def test_successful_enqueue_does_not_spool(tmp_path):
    spool = JobSpool(tmp_path / "spool")
    primary = _RecordingQueue()
    q = SpoolFallbackQueue(primary, spool)

    assert asyncio.run(q.push(_job())) == "queued"
    assert len(primary.pushed) == 1
    assert spool.pending() == []


def test_push_never_raises_even_when_the_spool_also_fails(tmp_path):
    """The hard guarantee: this runs inside call teardown. An exception here would mask the
    real teardown of a call that is already over. Loud ERROR, no exception."""
    unwritable = tmp_path / "nope"
    unwritable.write_text("i am a file, not a directory")
    q = SpoolFallbackQueue(_FailingQueue(), JobSpool(unwritable / "spool"))

    assert asyncio.run(q.push(_job())) == "dropped"


def test_drain_pushes_fifo_then_unlinks(tmp_path):
    spool = JobSpool(tmp_path / "spool")
    for sid in ("CA_a", "CA_b"):
        spool.write(_job(sid, ended_at=f"2026-07-26T10:0{sid[-1] == 'b'}0:00.000000+00:00"))
    primary = _RecordingQueue()

    moved = asyncio.run(spool.drain_into(primary))
    assert moved == 2
    assert [j.call_sid for j in primary.pushed] == ["CA_a", "CA_b"]
    assert spool.pending() == [], "drained files must be unlinked"


def test_drain_keeps_files_when_the_queue_is_still_down(tmp_path):
    """Unlink happens AFTER a successful push. A failed push must leave the job on disk —
    losing it here would defeat the entire point of the spool."""
    spool = JobSpool(tmp_path / "spool")
    spool.write(_job())

    assert asyncio.run(spool.drain_into(_FailingQueue())) == 0
    assert len(spool.pending()) == 1


def test_a_corrupt_spool_file_does_not_stop_the_drain(tmp_path):
    spool = JobSpool(tmp_path / "spool")
    spool.write(_job("CA_good"))
    (spool.directory / "00000000-CA_bad.json").write_text("{not json")
    primary = _RecordingQueue()

    moved = asyncio.run(spool.drain_into(primary))
    assert moved == 1
    assert [j.call_sid for j in primary.pushed] == ["CA_good"]
    assert len(spool.pending()) == 1, "the corrupt file stays on disk for a human"
