"""Metadata-only replay journals use a disposable Redis, never real call audio."""
import asyncio
from uuid import uuid4

from redis.asyncio import Redis
from tests.workers.test_dramatiq_worker import redis_url

__all__ = ["redis_url"]


def test_stream_reloads_identical_strict_metadata_with_bounded_expiry(redis_url):
    from roma.domain.audio.replay import ReplayRecord
    from roma.repositories.redis.replay_journal import RedisReplayJournal

    async def run():
        client = Redis.from_url(redis_url)
        try:
            journal = RedisReplayJournal(client, b"synthetic-only-key" * 2)
            lab_id = "voice-lab-" + uuid4().hex
            records = (ReplayRecord("epoch", 0.0, 1), ReplayRecord("drain", 20.0, 1))
            for index, record in enumerate(records):
                await journal.append(lab_id, index, record)
            assert await journal.read(lab_id) == records
            keys = await client.keys("roma:lab-replay:*")
            assert len(keys) == 1 and lab_id.encode() not in keys[0]
            assert 0 < await client.pttl(keys[0]) <= 600000
        finally:
            await client.aclose()
    asyncio.run(run())


def test_invalid_clock_or_unannounced_epoch_never_poisons_stream(redis_url):
    import pytest
    from roma.domain.audio.replay import ReplayRecord
    from roma.repositories.redis.replay_journal import RedisReplayJournal, ReplayJournalError

    async def run():
        client = Redis.from_url(redis_url)
        try:
            journal = RedisReplayJournal(client, b"synthetic-only-key" * 2)
            lab_id = "voice-lab-" + uuid4().hex
            first = ReplayRecord("epoch", 0.0, 1)
            second = ReplayRecord("drain", 20.0, 1)
            await journal.append(lab_id, 0, first)
            await journal.append(lab_id, 1, second)
            with pytest.raises(ReplayJournalError):
                await journal.append(lab_id, 2, ReplayRecord("drain", 10.0, 1))
            with pytest.raises(ReplayJournalError):
                await journal.append(lab_id, 2, ReplayRecord("drain", 30.0, 2))
            assert await journal.read(lab_id) == (first, second)
        finally:
            await client.aclose()
    asyncio.run(run())


def test_retry_is_idempotent_and_concurrent_append_has_one_winner(redis_url):
    import pytest
    from roma.domain.audio.replay import ReplayRecord
    from roma.repositories.redis.replay_journal import RedisReplayJournal, ReplayJournalError

    async def run():
        client = Redis.from_url(redis_url)
        try:
            journal = RedisReplayJournal(client, b"synthetic-only-key" * 2, max_entries=2)
            lab_id = "voice-lab-" + uuid4().hex
            first = ReplayRecord("epoch", 0.0, 1)
            results = await asyncio.gather(*(journal.append(lab_id, 0, first) for _ in range(10)))
            assert results.count(True) == 1 and results.count(False) == 9
            with pytest.raises(ReplayJournalError):
                await journal.append(lab_id, 0, ReplayRecord("epoch", 0.0, 2))
            with pytest.raises(ReplayJournalError):
                await journal.append(lab_id, 2, ReplayRecord("drain", 1.0, 1))
            await journal.append(lab_id, 1, ReplayRecord("flush", 20.0, 1, last_seq_no=0))
            assert len(await journal.read(lab_id)) == 2
            await journal.delete(lab_id)
            assert await journal.read(lab_id) == ()
        finally:
            await client.aclose()
    asyncio.run(run())


def test_corrupt_stream_read_has_a_fixed_sanitized_error(redis_url):
    import pytest
    from roma.domain.audio.replay import ReplayRecord
    from roma.repositories.redis.replay_journal import RedisReplayJournal, ReplayJournalError

    async def run():
        client = Redis.from_url(redis_url)
        try:
            journal = RedisReplayJournal(client, b"synthetic-only-key" * 2)
            lab_id = "voice-lab-" + uuid4().hex
            await journal.append(lab_id, 0, ReplayRecord("epoch", 0.0, 1))
            key = (await client.keys("roma:lab-replay:*"))[0]
            await client.xadd(key, {"record": "private-value-must-not-leak", "epoch": 1, "frames": 0}, id="2-0")
            with pytest.raises(ReplayJournalError, match=r"^Replay metadata is corrupt$"):
                await journal.read(lab_id)
        finally:
            await client.aclose()
    asyncio.run(run())
