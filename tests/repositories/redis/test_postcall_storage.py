"""The reliable queue and the recording store (docs/09, docs/06 §3).

fakeredis is constructed fresh INSIDE each test and injected via `client=`, matching
tests/controller/test_store.py. Async via `asyncio.run` — there is no pytest-asyncio.
"""

import asyncio
import json
import stat
import wave
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from roma.postcall.job import OUTCOME_LOCKED, PostcallJob
from roma.postcall.paths import DIR_MODE, FILE_MODE
from roma.postcall.queue import (
    DEAD_KEY,
    INFLIGHT_KEY,
    QUEUE_KEY,
    InMemoryPostcallQueue,
    RedisPostcallQueue,
    SpoolPostcallQueue,
)
from roma.postcall.spool import JobSpool
from roma.postcall.store import LocalRecordingStore, prune


def _job(call_sid="CA_q", **kw) -> PostcallJob:
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


def _client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def test_push_reserve_ack_round_trip():
    async def run():
        c = _client()
        q = RedisPostcallQueue(client=c)
        await q.push(_job())
        reserved = await q.reserve(timeout=1)
        mid = (await c.llen(QUEUE_KEY), await c.llen(INFLIGHT_KEY))
        await q.ack(reserved)
        end = (await c.llen(QUEUE_KEY), await c.llen(INFLIGHT_KEY))
        return reserved, mid, end

    reserved, mid, end = asyncio.run(run())
    assert reserved.call_sid == "CA_q"
    assert mid == (0, 1), "reserve must move the job into the inflight list, atomically"
    assert end == (0, 0), "ack must remove it"


def test_a_worker_killed_before_ack_leaves_the_job_recoverable():
    """The durability guarantee docs/09 asks for, stated as a test.

    `BLMOVE` is atomic, so the job is in exactly one list at every instant. A worker that
    dies between reserve and ack leaves it in `:inflight` — visible and recoverable, never
    lost — and the next worker's `recover_inflight()` puts it back.
    """

    async def run():
        c = _client()
        await RedisPostcallQueue(client=c).push(_job())
        await RedisPostcallQueue(client=c).reserve(timeout=1)
        after_death = (await c.llen(QUEUE_KEY), await c.llen(INFLIGHT_KEY))

        q2 = RedisPostcallQueue(client=c)
        recovered = await q2.recover_inflight()
        redelivered = await q2.reserve(timeout=1)
        return after_death, recovered, redelivered

    after_death, recovered, redelivered = asyncio.run(run())
    assert after_death == (0, 1), "the job must survive somewhere"
    assert recovered == 1
    assert redelivered.call_sid == "CA_q"


def test_ack_matches_the_exact_pushed_payload():
    """`LREM` matches by exact string. If `ack` re-serialised the job — a different key
    order, a different float repr — it would match nothing, the inflight entry would leak
    forever, and every restart would redeliver the same job."""

    async def run():
        c = _client()
        q = RedisPostcallQueue(client=c)
        await q.push(_job())
        reserved = await q.reserve(timeout=1)
        rebuilt = PostcallJob(**{k: v for k, v in vars(reserved).items() if k != "raw"})
        assert rebuilt == reserved and rebuilt.raw is None
        await q.ack(reserved)
        return await c.llen(INFLIGHT_KEY)

    assert asyncio.run(run()) == 0


def test_reserve_returns_none_when_the_queue_is_empty():
    async def run():
        return await RedisPostcallQueue(client=_client()).reserve(timeout=1)

    assert asyncio.run(run()) is None


def test_retry_bumps_attempts_and_requeues():
    async def run():
        c = _client()
        q = RedisPostcallQueue(client=c)
        await q.push(_job())
        first = await q.reserve(timeout=1)
        await q.retry(first)
        second = await q.reserve(timeout=1)
        return first.attempts, second.attempts, await c.llen(INFLIGHT_KEY)

    first, second, inflight = asyncio.run(run())
    assert (first, second) == (0, 1)
    assert inflight == 1


def test_dead_letters_leave_the_main_queue_clean():
    async def run():
        c = _client()
        q = RedisPostcallQueue(client=c)
        await q.push(_job())
        j = await q.reserve(timeout=1)
        await q.dead(j)
        return await c.llen(QUEUE_KEY), await c.llen(INFLIGHT_KEY), await c.llen(DEAD_KEY)

    assert asyncio.run(run()) == (0, 0, 1)


def test_redis_queue_requires_a_url_or_a_client():
    with pytest.raises(ValueError, match="redis_url or a client"):
        RedisPostcallQueue()


def test_in_memory_queue_is_fifo():
    async def run():
        q = InMemoryPostcallQueue()
        for sid in ("CA_1", "CA_2", "CA_3"):
            await q.push(_job(sid))
        return [(await q.reserve()).call_sid for _ in range(3)]

    assert asyncio.run(run()) == ["CA_1", "CA_2", "CA_3"]


def test_spool_queue_runs_the_whole_path_without_redis(tmp_path):
    """Redis is not running on this box. `--queue=spool` must exercise the real flow, not
    a mock, or the post-call path could only ever be half-tested here."""

    async def run():
        q = SpoolPostcallQueue(JobSpool(tmp_path / "spool"))
        await q.push(_job())
        reserved = await q.reserve()
        still_there = len(q._spool.pending())
        await q.ack(reserved)
        return reserved.call_sid, still_there, len(q._spool.pending())

    sid, before_ack, after_ack = asyncio.run(run())
    assert sid == "CA_q"
    assert before_ack == 1, "an unacked job must survive a crash — it stays on disk"
    assert after_ack == 0


def _raw_pcm(tmp_path, frames=800):
    """Stereo PCM16: left = lead, right = Roma."""
    raw = tmp_path / "capture.s16le"
    raw.write_bytes(b"\x01\x02\x03\x04" * frames)
    return raw


def test_put_writes_a_valid_wav_and_sidecar(tmp_path):
    store = LocalRecordingStore(tmp_path / "recordings")
    job = _job()
    wav_path = asyncio.run(store.put(job, _raw_pcm(tmp_path)))

    assert wav_path == tmp_path / "recordings" / "2026-07-26" / "CA_q.wav"
    with wave.open(str(wav_path), "rb") as w:
        assert w.getnchannels() == 2
        assert w.getsampwidth() == 2
        assert w.getframerate() == 8000
        assert w.getnframes() == 800

    meta = json.loads((wav_path.parent / "CA_q.json").read_text())
    assert meta == {
        "v": 1,
        "call_sid": "CA_q",
        "timestamp": "2026-07-26T10:00:00+00:00",
        "duration": 338.2,
        "outcome": "locked",
        "locked_slot": "2026-07-27T15:00:00+05:30",
    }


def test_stored_artifacts_are_owner_only(tmp_path):
    """docs/07: recordings are access-controlled and not world-readable."""
    store = LocalRecordingStore(tmp_path / "recordings")
    wav = asyncio.run(store.put(_job(), _raw_pcm(tmp_path)))

    assert stat.S_IMODE(wav.stat().st_mode) == FILE_MODE
    assert stat.S_IMODE((wav.parent / "CA_q.json").stat().st_mode) == FILE_MODE
    assert stat.S_IMODE(wav.parent.stat().st_mode) == DIR_MODE
    assert stat.S_IMODE(wav.parent.parent.stat().st_mode) == DIR_MODE


def test_put_leaves_no_temp_files(tmp_path):
    store = LocalRecordingStore(tmp_path / "recordings")
    wav = asyncio.run(store.put(_job(), _raw_pcm(tmp_path)))
    assert not list(wav.parent.glob("*.tmp"))


def test_put_is_idempotent(tmp_path):
    """At-least-once delivery is only safe because a redelivery rewrites identical bytes."""
    store = LocalRecordingStore(tmp_path / "recordings")
    raw = _raw_pcm(tmp_path)
    first = asyncio.run(store.put(_job(), raw))
    first_bytes = first.read_bytes()
    second = asyncio.run(store.put(_job(), raw))

    assert first == second
    assert second.read_bytes() == first_bytes
    assert len(list(first.parent.iterdir())) == 2


def test_already_stored_requires_BOTH_artifacts(tmp_path):
    """The crash window is a worker killed between the two renames. Treating a WAV with no
    sidecar as "done" would leave a permanently unlabelled recording."""
    store = LocalRecordingStore(tmp_path / "recordings")
    job = _job()
    assert store.already_stored(job) is False

    wav = asyncio.run(store.put(job, _raw_pcm(tmp_path)))
    assert store.already_stored(job) is True

    (wav.parent / "CA_q.json").unlink()
    assert store.already_stored(job) is False


def test_recording_lands_in_the_day_the_call_happened(tmp_path):
    """Not "today": a job drained from the spool days later must still partition by the
    call's own date, or retention would apply the wrong window to it."""
    store = LocalRecordingStore(tmp_path / "recordings")
    job = _job(started_at="2026-01-15T08:30:00+00:00")
    wav = asyncio.run(store.put(job, _raw_pcm(tmp_path)))
    assert wav.parent.name == "2026-01-15"


def _day(root, name, files=2):
    d = root / name
    d.mkdir(parents=True)
    for i in range(files):
        (d / f"CA_{i}.wav").write_bytes(b"\x00\x00")
    return d


def test_prune_removes_days_past_the_window(tmp_path):
    root = tmp_path / "recordings"
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    old = _day(root, (now - timedelta(days=100)).strftime("%Y-%m-%d"))
    recent = _day(root, (now - timedelta(days=10)).strftime("%Y-%m-%d"))

    assert prune(root, retention_days=90, now=now) == 1
    assert not old.exists()
    assert recent.exists()


def test_prune_never_removes_today(tmp_path):
    """A misconfigured window must not delete a call that is still being written."""
    root = tmp_path / "recordings"
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    today = _day(root, now.strftime("%Y-%m-%d"))

    assert prune(root, retention_days=0, now=now) == 0
    assert today.exists()


def test_prune_ignores_directories_that_are_not_day_partitions(tmp_path):
    root = tmp_path / "recordings"
    stray = _day(root, "unknown-date")
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

    assert prune(root, retention_days=1, now=now) == 0
    assert stray.exists(), "leave anything unrecognised for a human, never guess"


def test_prune_on_a_missing_root_is_a_no_op(tmp_path):
    assert prune(tmp_path / "nope", 90, datetime(2026, 7, 26, tzinfo=UTC)) == 0
