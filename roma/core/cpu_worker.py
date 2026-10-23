"""Bounded process executor for pure-Python scans/resampling, separate from model work."""

import asyncio
import math
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from functools import partial

from roma.core.async_queue import QueuePressure


def _configure_worker():
    from roma.core.logging import configure_logging
    configure_logging()


class CpuWorker:
    def __init__(self, *, workers=2, max_pending=8, admission_timeout=2):
        if type(workers) is not int or type(max_pending) is not int or not 1 <= workers <= max_pending <= 16:
            raise ValueError("Invalid CPU worker limits")
        if not math.isfinite(admission_timeout) or not 0 < admission_timeout <= 30:
            raise ValueError("Invalid CPU admission timeout")
        self._pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"), initializer=_configure_worker)
        self._slots = asyncio.Semaphore(max_pending)
        self.admission_timeout = admission_timeout
        self.pending = 0
        self._waiting = 0
        self._max_waiting = max_pending
        self.closed = False

    async def run(self, function, *args):
        if self.closed:
            raise RuntimeError("CPU worker closed")
        if self._waiting >= self._max_waiting:
            raise QueuePressure()
        self._waiting += 1
        try:
            try:
                async with asyncio.timeout(self.admission_timeout):
                    await self._slots.acquire()
            except TimeoutError:
                raise QueuePressure() from None
        finally:
            self._waiting -= 1
        loop = asyncio.get_running_loop()
        self.pending += 1
        try:
            future = self._pool.submit(partial(function, *args))
        except BaseException:
            self.pending -= 1
            self._slots.release()
            raise
        def release():
            self.pending -= 1
            self._slots.release()
        # Cancellation abandons the result, not the running process job or its lease.
        future.add_done_callback(lambda _future: loop.call_soon_threadsafe(release))
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        return await asyncio.shield(wrapped)

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        await asyncio.to_thread(self._pool.shutdown, wait=True, cancel_futures=True)
        await asyncio.sleep(0)
