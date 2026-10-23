import asyncio

import pytest
from roma.core.async_queue import BoundedChannel, QueuePolicy, QueuePressure
from roma.services.streaming_text import PunctuationChunker


def test_queue_bounds_drop_oldest_and_hysteresis():
    async def run():
        q = BoundedChannel("partials", capacity=4, high=3, low=1, policy=QueuePolicy.DROP_OLDEST)
        for n in range(10):
            assert await q.put(n)
        assert q.depth == 3 and q.metrics.dropped_oldest == 7
        assert [await q.get(), await q.get()] == [7, 8]
        assert not q.pressured
        assert await q.get() == 9
        await q.close()
        with pytest.raises(StopAsyncIteration):
            await q.get()
    asyncio.run(run())


def test_drop_newest_preserves_queued_order_and_closed_queue_rejects():
    async def run():
        q = BoundedChannel("audio", capacity=2, high=2, low=0, policy=QueuePolicy.DROP_NEWEST)
        assert await q.put(1) and await q.put(2)
        assert not await q.put(3)
        assert q.metrics.dropped_newest == 1
        assert await q.get() == 1 and await q.get() == 2
        await q.close()
        assert not await q.put(4)
    asyncio.run(run())


def test_speech_backpressure_waits_then_resumes_below_low_watermark():
    async def run():
        q = BoundedChannel("tts_text", capacity=3, high=2, low=0,
                           policy=QueuePolicy.DROP_NEWEST, wait_timeout=1)
        await q.put(1)
        await q.put(2)
        pending = asyncio.create_task(q.put(3))
        await asyncio.sleep(0)
        assert not pending.done()
        assert await q.get() == 1
        await asyncio.sleep(0)
        assert not pending.done()
        assert await q.get() == 2
        assert await pending and await q.get() == 3
        assert q.metrics.wait_ms > 0 and q.metrics.peak_depth <= 2
        await q.close()
    asyncio.run(run())


def test_outbound_timeout_is_counted_and_loud():
    async def run():
        q = BoundedChannel("tts_audio", capacity=1, high=1, low=0,
                           policy=QueuePolicy.DROP_NEWEST, wait_timeout=0.01)
        await q.put("first")
        with pytest.raises(QueuePressure, match=r"^Queue pressure$"):
            await q.put("never silently lost")
        assert q.metrics.dropped_newest == 1
        assert await q.get() == "first"
        await q.close()
    asyncio.run(run())


@pytest.mark.parametrize("high,low", [(0, 0), (5, 1), (2, 2), (2, -1)])
def test_bad_watermarks_are_rejected(high, low):
    with pytest.raises(ValueError):
        BoundedChannel("tokens", capacity=4, high=high, low=low)


def test_punctuation_chunker_streams_clauses_preserves_numerals_and_bounds_tail():
    chunker = PunctuationChunker(max_chars=64)
    assert chunker.feed("नमस्ते,") == ("नमस्ते,",)
    assert chunker.feed(" fee 1,") == ()
    assert chunker.feed("000.50? Next") == ("fee 1,000.50?",)
    assert chunker.finish() == ("Next",)
    with pytest.raises(ValueError):
        PunctuationChunker(max_chars=8).feed("x" * 9)


def _slow_cpu(value):
    import time
    time.sleep(0.15)
    return value


def test_cpu_pool_keeps_websocket_heartbeat_live_and_retains_lease_until_work_finishes():
    from roma.core.cpu_worker import CpuWorker
    async def run():
        worker = CpuWorker(workers=1, max_pending=1)
        try:
            task = asyncio.create_task(worker.run(_slow_cpu, 7))
            ticks = 0
            while not task.done():
                ticks += 1
                await asyncio.sleep(0.005)
            assert await task == 7 and ticks > 3
            task = asyncio.create_task(worker.run(_slow_cpu, 8))
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert worker.pending == 1
            assert await worker.run(_slow_cpu, 9) == 9
        finally:
            await worker.aclose()
        assert worker.pending == 0
    asyncio.run(run())
