"""Local job spool: the durability fallback when the queue push fails (docs/09).

docs/09 is unambiguous — "a killed worker must never silently drop a recording". But
Redis is not always reachable (it is not running locally at all today), and call teardown
must never fail because of post-call bookkeeping. Those two rules only coexist if a failed
enqueue lands somewhere durable instead of being logged and forgotten.

So: push to the real queue; on ANY failure write the job to a file here; the worker drains
the directory on startup and on every idle poll. Nothing is lost, and nothing can raise
out of the teardown path.

Note the two different spools in this package, easy to conflate:

  media spool  {root}/media/{date}/{sid}.s16le  — the audio itself, written during the call
  job spool    {root}/spool/postcall/*.json     — THIS: queue-failure fallback, one per job
"""

import contextlib
import logging
import os
from pathlib import Path

from roma.postcall.job import PostcallJob
from roma.postcall.paths import ensure_private_dir, open_private

_log = logging.getLogger("roma.postcall")

_TMP_SUFFIX = ".tmp"
_JOB_SUFFIX = ".json"


def _job_filename(job: PostcallJob) -> str:
    """Sortable, collision-free, and PII-free.

    Timestamp-first means a lexicographic sort of the directory IS the FIFO drain order,
    with no need to stat anything. The `ended_at` stamp carries sub-second precision
    precisely so a redial inside the same second cannot overwrite the earlier job.
    """
    stamp = "".join(ch for ch in job.ended_at if ch.isalnum())
    return f"{stamp}-{job.call_sid}{_JOB_SUFFIX}"


class JobSpool:
    """A directory of pending jobs, written atomically."""

    def __init__(self, directory) -> None:
        self._dir = Path(directory)

    @property
    def directory(self) -> Path:
        return self._dir

    def write(self, job: PostcallJob) -> Path:
        """Persist one job. Atomic: a reader never sees a half-written file.

        temp-in-the-same-directory -> fsync -> os.replace -> fsync the directory. The
        temp file must share the directory so `os.replace` stays on one filesystem (across
        filesystems it is not atomic). The final directory fsync is what makes the RENAME
        durable, not just the bytes — without it a power loss can leave the file written
        but not linked, which is exactly the silent drop docs/09 forbids.
        """
        ensure_private_dir(self._dir)
        final = self._dir / _job_filename(job)
        tmp = final.with_suffix(final.suffix + _TMP_SUFFIX)
        with open_private(tmp, "w") as fh:
            fh.write(job.to_json())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
        self._fsync_dir()
        return final

    def _fsync_dir(self) -> None:
        with contextlib.suppress(OSError):
            fd = os.open(self._dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def pending(self) -> "list[Path]":
        """Spooled jobs in FIFO order. Missing directory means nothing pending."""
        if not self._dir.is_dir():
            return []
        return sorted(p for p in self._dir.iterdir() if p.suffix == _JOB_SUFFIX)

    def load(self, path: Path) -> "PostcallJob | None":
        """Read one spooled job, or None if it is unreadable/corrupt (logged, not raised)."""
        try:
            return PostcallJob.from_raw(Path(path).read_text())
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the drain
            _log.error("postcall: unreadable spool file %s (%s)", path.name, type(exc).__name__)
            return None

    async def drain_into(self, queue) -> int:
        """Push spooled jobs to `queue`, oldest first. Returns how many moved.

        Unlink happens AFTER a successful push, never before: a crash in between yields a
        duplicate, and the worker is idempotent, so a duplicate is free. The reverse order
        would lose the job outright.

        Stops at the first failure and leaves the rest in place — a failed push means the
        queue is still down, so continuing would just churn.
        """
        moved = 0
        for path in self.pending():
            job = self.load(path)
            if job is None:
                continue
            try:
                await queue.push(job)
            except Exception as exc:  # noqa: BLE001 — queue still down; try again later
                _log.warning(
                    "postcall: spool drain stopped, queue unavailable (%s); %d moved, rest kept",
                    type(exc).__name__,
                    moved,
                )
                return moved
            path.unlink(missing_ok=True)
            moved += 1
        if moved:
            _log.info("postcall: drained %d spooled job(s) into the queue", moved)
        return moved


class SpoolFallbackQueue:
    """A producer that writes to a spool file when the real queue push fails.

    Deliberately a DECORATOR rather than a branch inside `RedisPostcallQueue`: the Redis
    queue stays honest (it raises, so the worker can distinguish "down" from "empty") and
    the fallback policy lives in one small class with its own tests.

    `push` NEVER raises. It is called from the call-teardown path, where an exception
    would mask the real teardown — and where there is nothing useful to propagate to
    anyway, because the call is already over.
    """

    def __init__(self, primary, spool: JobSpool) -> None:
        self._primary = primary
        self._spool = spool

    async def push(self, job: PostcallJob) -> str:
        """Enqueue, or spool. Returns "queued" | "spooled" | "dropped" for the caller's log."""
        try:
            await self._primary.push(job)
            return "queued"
        except Exception as exc:  # noqa: BLE001 — fall back, never fail teardown
            _log.warning(
                "postcall: enqueue failed (%s); spooling call_sid=%s",
                type(exc).__name__,
                job.call_sid,
            )
        try:
            self._spool.write(job)
            return "spooled"
        except Exception as exc:  # noqa: BLE001 — last resort; still must not raise
            _log.error(
                "postcall: enqueue AND spool both failed (%s); recording reference LOST "
                "for call_sid=%s ref=%s",
                type(exc).__name__,
                job.call_sid,
                job.recording_ref,
            )
            return "dropped"

    async def aclose(self) -> None:
        aclose = getattr(self._primary, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


__all__ = ["JobSpool", "SpoolFallbackQueue"]
