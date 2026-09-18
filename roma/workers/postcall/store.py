"""Durable recording storage + retention (docs/09, docs/07).

docs/09's naming is `{date}/{call_sid}.{ext}` — "sortable, unique, ties back to
call-state". The date partition is also what makes retention cheap: `prune` deletes whole
day-directories instead of stat-walking every file.

**"Durably written" is the load-bearing phrase**, because the queue is only allowed to ack
after it. Here it means: the WAV and its metadata sidecar are both fully written, fsynced,
and renamed into place. Acking after the WAV but before the sidecar would leave an
unlabelled recording — audio nobody can tie to a call outcome — which is exactly the state
docs/09's metadata rule exists to prevent.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from roma.workers.postcall.job import PostcallJob
from roma.workers.postcall.paths import DIR_MODE, FILE_MODE, ensure_private_dir

_log = logging.getLogger("roma.workers.postcall")

_COPY_CHUNK_BYTES = 64 * 1024

_DATE_FMT = "%Y-%m-%d"


@runtime_checkable
class RecordingStore(Protocol):
    async def put(self, job: PostcallJob, raw_path: Path) -> Path: ...
    def already_stored(self, job: PostcallJob) -> bool: ...


def _day_of(job: PostcallJob) -> str:
    """The date partition, from the job's own start stamp — never `today`.

    A job drained from the spool days later must still land in the day the call happened,
    or retention would keep it for the wrong window and the partition would stop being a
    reliable index.
    """
    try:
        return datetime.fromisoformat(job.started_at.replace("Z", "+00:00")).strftime(_DATE_FMT)
    except ValueError:
        return "unknown-date"


class LocalRecordingStore:
    """Writes recordings to an owner-only directory tree on local disk.

    Local, not a bucket: docs/09 option 2 and the Vadodara on-prem decision both say lead
    audio stays on our own infrastructure.
    """

    def __init__(self, root) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _final_paths(self, job: PostcallJob) -> "tuple[Path, Path]":
        day = self._root / _day_of(job)
        return day / f"{job.call_sid}.wav", day / f"{job.call_sid}.json"

    def already_stored(self, job: PostcallJob) -> bool:
        """True only if BOTH artifacts are present.

        Deliberately not "the WAV exists": the crash window this guards is a worker killed
        between the two renames, and treating a WAV with no sidecar as done would leave a
        permanently unlabelled recording.
        """
        wav, meta = self._final_paths(job)
        return wav.exists() and meta.exists()

    async def put(self, job: PostcallJob, raw_path: Path) -> Path:
        """Convert the raw capture to WAV, write the sidecar, and return the WAV path.

        Returns only once everything is durable. Every write is
        temp -> fsync -> chmod -> `os.replace`, so a crash at any point leaves either the
        previous state or the complete new state, never a half-file. The final directory
        fsync makes the renames themselves durable.

        The work runs in a worker thread: it is a bounded but genuinely blocking sequence
        (a multi-megabyte copy plus three fsyncs), and holding the event loop through it
        would stall the shutdown signal handler for the whole conversion.
        """
        return await asyncio.to_thread(self._put_sync, job, Path(raw_path))

    def _put_sync(self, job: PostcallJob, raw_path: Path) -> Path:
        """The blocking body of `put`. Kept separate so it is directly unit-testable."""
        wav_path, meta_path = self._final_paths(job)
        day_dir = ensure_private_dir(wav_path.parent)

        wav_tmp = wav_path.with_suffix(".wav.tmp")
        with open(wav_tmp, "wb") as fh:
            with wave.open(fh, "wb") as wav:
                wav.setnchannels(job.num_channels)
                wav.setsampwidth(2)
                wav.setframerate(job.sample_rate)
                with open(raw_path, "rb") as src:
                    while chunk := src.read(_COPY_CHUNK_BYTES):
                        wav.writeframes(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(wav_tmp, FILE_MODE)
        os.replace(wav_tmp, wav_path)

        meta_tmp = meta_path.with_suffix(".json.tmp")
        with open(meta_tmp, "w") as fh:
            json.dump(job.sidecar(), fh, sort_keys=True, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(meta_tmp, FILE_MODE)
        os.replace(meta_tmp, meta_path)

        _fsync_dir(day_dir)
        _log.info(
            "postcall: stored recording call_sid=%s outcome=%s duration=%.1fs",
            job.call_sid,
            job.outcome,
            job.duration_secs,
        )
        return wav_path


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def prune(root, retention_days: int, now: datetime) -> int:
    """Delete day-directories older than the retention window. Returns days removed.

    docs/07 and docs/09 both require a defined retention window — recordings are PII and
    "keep forever" is not a policy. This is why the store is partitioned by date: expiry is
    an `rmtree` of whole directories, not a walk over every file's mtime.

    The CURRENT day is never pruned, even at `retention_days=0`, so a misconfiguration
    cannot delete a call that is still being written.
    """
    root = Path(root)
    if not root.is_dir() or retention_days < 0:
        return 0

    cutoff = (now - timedelta(days=retention_days)).date()
    today = now.date()
    removed = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            day = datetime.strptime(child.name, _DATE_FMT).date()
        except ValueError:
            continue
        if day >= today or day >= cutoff:
            continue
        count = sum(1 for _ in child.iterdir())
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
        _log.info(
            "postcall: retention pruned %s (%d files, older than %d days)",
            child.name,
            count,
            retention_days,
        )
    return removed


def describe_permissions(root) -> str:
    """A one-line startup log of the store's actual mode.

    Logged rather than asserted so a misconfigured 0o755 is VISIBLE — docs/07 requires the
    store not be world-readable, and a silent wrong mode is the failure that never surfaces.
    """
    root = Path(root)
    if not root.exists():
        return f"{root} (not created yet, will be {DIR_MODE:o})"
    mode = os.stat(root).st_mode & 0o777
    flag = "OK" if mode == DIR_MODE else f"EXPECTED {DIR_MODE:o} — NOT owner-only"
    return f"{root} mode={mode:o} ({flag})"


__all__ = ["RecordingStore", "LocalRecordingStore", "prune", "describe_permissions"]
