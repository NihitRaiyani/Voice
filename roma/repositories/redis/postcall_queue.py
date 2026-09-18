"""The `queue:postcall` reliable queue (docs/06 §3, docs/09, docs/08).

Mirrors `roma.repositories.redis.conversation_state`'s seam exactly — a Protocol, an in-memory implementation
for tests, and a Redis one whose `__init__` takes a URL (prod) or an injected client
(tests use fakeredis) — with the `redis` import kept lazy so this package imports on a box
that has no redis installed.

## Why a list, not a stream

Producer `LPUSH`; consumer `BLMOVE(queue, inflight, timeout, RIGHT, LEFT)`; ack is
`LREM(inflight, 1, raw)`. `BLMOVE` is atomic (RPOPLPUSH semantics), so a job is never in
neither list: a worker killed anywhere between reserve and ack leaves it in `:inflight`,
where `recover_inflight()` finds it at startup. That is precisely docs/09's "the job is
acked ONLY after the recording is durably written".

Consumer groups would buy the same at-least-once guarantee for strictly more machinery at
v1's scale — an idempotent `XGROUP CREATE ... MKSTREAM` bootstrap, `XAUTOCLAIM` for
recovery, and a `MAXLEN` trimming policy to stop unbounded growth. `BLMOVE` needs none of
it. It also matters that fakeredis's list operations are far better exercised than its
consumer-group emulation, because the repo's entire Redis test posture is fakeredis.

At-least-once redelivery is safe because the worker is idempotent: the destination path is
deterministic and writes go temp -> fsync -> `os.replace`.

## Multiple workers

docs/08 says "worker pool". `BLMOVE` is atomic, so N processes can consume this queue
safely with no extra work. v1 runs one worker per process; scale by running more
processes. There is deliberately no in-process pool.
"""

import asyncio
import contextlib
import logging
from typing import Protocol, runtime_checkable

from roma.workers.postcall.job import PostcallJob

_log = logging.getLogger("roma.workers.postcall")

QUEUE_KEY = "queue:postcall"
INFLIGHT_KEY = "queue:postcall:inflight"
DEAD_KEY = "queue:postcall:dead"

CONNECT_TIMEOUT_SECS = 2
SOCKET_TIMEOUT_SECS = 2
PUSH_TIMEOUT_SECS = 3


@runtime_checkable
class PostcallProducer(Protocol):
    """What the CALL path depends on — nothing more. Keeping the producer surface this
    narrow is what lets `media.py` accept a plain spool, a fake, or the real queue."""

    async def push(self, job: PostcallJob) -> None: ...


@runtime_checkable
class PostcallQueue(PostcallProducer, Protocol):
    """The full surface the worker needs."""

    async def reserve(self, timeout: int = 5) -> "PostcallJob | None": ...  # noqa: ASYNC109
    async def ack(self, job: PostcallJob) -> None: ...
    async def retry(self, job: PostcallJob) -> None: ...
    async def dead(self, job: PostcallJob) -> None: ...
    async def recover_inflight(self) -> int: ...


class InMemoryPostcallQueue:
    """Process-local queue for tests and offline runs.

    Serialises through the same `to_json`/`from_raw` path as Redis, so a test exercises the
    real payload shape — including the `raw` string the ack depends on.
    """

    def __init__(self) -> None:
        self._pending: list[str] = []
        self._inflight: list[str] = []
        self.dead_letters: list[str] = []

    async def push(self, job: PostcallJob) -> None:
        self._pending.insert(0, job.to_json())

    async def reserve(self, timeout: int = 5) -> "PostcallJob | None":  # noqa: ASYNC109
        if not self._pending:
            return None
        raw = self._pending.pop()
        self._inflight.insert(0, raw)
        return PostcallJob.from_raw(raw)

    async def ack(self, job: PostcallJob) -> None:
        with contextlib.suppress(ValueError):
            self._inflight.remove(job.raw if job.raw is not None else job.to_json())

    async def retry(self, job: PostcallJob) -> None:
        await self.ack(job)
        await self.push(job.with_attempt())

    async def dead(self, job: PostcallJob) -> None:
        await self.ack(job)
        self.dead_letters.append(job.raw if job.raw is not None else job.to_json())

    async def recover_inflight(self) -> int:
        n = len(self._inflight)
        self._pending.extend(self._inflight)
        self._inflight.clear()
        return n

    def depth(self) -> int:
        return len(self._pending)

    def inflight_depth(self) -> int:
        return len(self._inflight)


class RedisPostcallQueue:
    """Redis-backed reliable queue (docs/06 §3).

    Pass a `redis_url` (prod, from `config.redis_url`) or inject an async client (tests use
    fakeredis). The `redis` import is lazy so this package imports cleanly without redis.
    """

    def __init__(self, redis_url: "str | None" = None, *, client=None) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("RedisPostcallQueue needs a redis_url or a client")
            from redis import asyncio as redis_asyncio

            client = redis_asyncio.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=CONNECT_TIMEOUT_SECS,
                socket_timeout=SOCKET_TIMEOUT_SECS,
            )
        self._r = client

    @staticmethod
    def _decode(value) -> str:
        return value.decode() if isinstance(value, bytes) else value

    async def push(self, job: PostcallJob) -> None:
        await asyncio.wait_for(self._r.lpush(QUEUE_KEY, job.to_json()), PUSH_TIMEOUT_SECS)

    async def reserve(self, timeout: int = 5) -> "PostcallJob | None":  # noqa: ASYNC109
        """Atomically move one job to the inflight list and return it, or None on timeout.

        `BLMOVE` is the whole durability story: the job is in exactly one of the two lists
        at every instant, so a crash cannot lose it.
        """
        raw = await self._r.blmove(QUEUE_KEY, INFLIGHT_KEY, timeout, "RIGHT", "LEFT")
        if raw is None:
            return None
        return PostcallJob.from_raw(self._decode(raw))

    async def ack(self, job: PostcallJob) -> None:
        """Remove the job from the inflight list. Call ONLY after a durable write.

        `LREM` matches by EXACT STRING, which is why `job.raw` exists and why it must not
        be re-serialised: a differing key order or float repr would match nothing, the
        entry would sit in `:inflight` forever, and `recover_inflight()` would redeliver
        the same job on every restart.
        """
        raw = job.raw if job.raw is not None else job.to_json()
        removed = await self._r.lrem(INFLIGHT_KEY, 1, raw)
        if not removed:
            _log.error(
                "postcall: ack matched no inflight entry for call_sid=%s — the payload was "
                "re-serialised somewhere; the job will be redelivered",
                job.call_sid,
            )

    async def retry(self, job: PostcallJob) -> None:
        await self.ack(job)
        await self.push(job.with_attempt())

    async def dead(self, job: PostcallJob) -> None:
        raw = job.raw if job.raw is not None else job.to_json()
        await self._r.lpush(DEAD_KEY, raw)
        await self.ack(job)
        _log.error(
            "postcall: job dead-lettered after %d attempts, call_sid=%s",
            job.attempts,
            job.call_sid,
        )

    async def recover_inflight(self) -> int:
        """Return everything a previous worker left inflight to the pending queue.

        Runs at worker startup. Without it, a job whose worker was killed mid-store would
        sit in `:inflight` indefinitely — present, but never processed.
        """
        moved = 0
        while await self._r.lmove(INFLIGHT_KEY, QUEUE_KEY, "RIGHT", "LEFT") is not None:
            moved += 1
        if moved:
            _log.warning(
                "postcall: recovered %d job(s) left inflight by a previous worker", moved
            )
        return moved

    async def depth(self) -> int:
        return await self._r.llen(QUEUE_KEY)

    async def aclose(self) -> None:
        aclose = getattr(self._r, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


class SpoolPostcallQueue:
    """The full queue protocol backed by the job-spool directory — no Redis at all.

    This is not a toy. Redis is not running on the current dev box, so without it the
    post-call path could only ever be half-exercised. `run_postcall_worker.py --queue=spool`
    runs the entire flow — reserve, store, ack, retry, dead-letter — against the same files
    the production fallback writes.

    Single-consumer only: reservation is "read the file", with no cross-process lock. That
    is correct for its purpose (one dev worker, or draining a spool after an outage).
    """

    def __init__(self, spool) -> None:
        self._spool = spool
        self._inflight: dict[str, object] = {}

    async def push(self, job: PostcallJob) -> None:
        self._spool.write(job)

    async def reserve(self, timeout: int = 5) -> "PostcallJob | None":  # noqa: ASYNC109
        for path in self._spool.pending():
            job = self._spool.load(path)
            if job is None:
                continue
            self._inflight[job.call_sid] = path
            return job
        return None

    async def ack(self, job: PostcallJob) -> None:
        path = self._inflight.pop(job.call_sid, None)
        if path is not None:
            path.unlink(missing_ok=True)

    async def retry(self, job: PostcallJob) -> None:
        await self.ack(job)
        await self.push(job.with_attempt())

    async def dead(self, job: PostcallJob) -> None:
        await self.ack(job)
        _log.error(
            "postcall: job dead-lettered after %d attempts, call_sid=%s",
            job.attempts,
            job.call_sid,
        )

    async def recover_inflight(self) -> int:
        self._inflight.clear()
        return 0


__all__ = [
    "PostcallProducer",
    "PostcallQueue",
    "InMemoryPostcallQueue",
    "RedisPostcallQueue",
    "SpoolPostcallQueue",
    "QUEUE_KEY",
    "INFLIGHT_KEY",
    "DEAD_KEY",
]
