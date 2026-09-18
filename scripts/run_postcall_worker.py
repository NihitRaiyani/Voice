"""Run the post-call worker (docs/09, docs/10 Step 6).

    uv run python scripts/run_postcall_worker.py                # Redis-backed
    uv run python scripts/run_postcall_worker.py --queue=spool  # no Redis at all
    uv run python scripts/run_postcall_worker.py --drain        # empty it, then exit

`--queue=spool` is not a toy: Redis is not running on the dev box, and this runs the whole
flow — reserve, store, ack, retry, retention — against the same spool files the production
fallback writes.

`--drain` is what a deploy script calls before taking the box down (docs/08: "flush the
post-call queue, then exit").

This is a SEPARATE PROCESS from the media server on purpose (docs/08: slow work never in
the pipeline), and `roma.workers.postcall` imports no pipecat, so it starts without dragging in
silero/torch.
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roma.core.config import get_settings
from roma.core.logging import configure_logging
from roma.domain.calls import CONSENT_LINE, consent_signed_off
from roma.repositories.redis.postcall_queue import (
    RedisPostcallQueue,
    SpoolPostcallQueue,
)
from roma.workers.postcall.paths import job_spool_dir, media_dir, recordings_dir
from roma.workers.postcall.spool import JobSpool
from roma.workers.postcall.store import LocalRecordingStore, describe_permissions
from roma.workers.postcall.worker import WorkerDeps, run_worker

_log = logging.getLogger("roma.workers.postcall")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Roma post-call recording worker")
    p.add_argument(
        "--queue",
        choices=("redis", "spool"),
        default="redis",
        help="'spool' runs the full path with no Redis (the current dev box).",
    )
    p.add_argument(
        "--drain",
        action="store_true",
        help="Process until the queue is empty, then exit (call before shutdown).",
    )
    return p.parse_args(argv)


async def _run(args) -> int:
    settings = get_settings()
    spool = JobSpool(job_spool_dir(settings))
    store = LocalRecordingStore(recordings_dir(settings))

    if args.queue == "spool":
        queue, spool_for_drain = SpoolPostcallQueue(spool), None
    else:
        queue, spool_for_drain = (
            RedisPostcallQueue(settings.redis_url.get_secret_value()),
            spool,
        )

    if not consent_signed_off(CONSENT_LINE):
        _log.error(
            "postcall: consent wording is still the PLACEHOLDER — recordings will be "
            "DISCARDED, not stored (CLAUDE.md gate 4). Jobs still drain normally."
        )
    _log.info("postcall: store %s", describe_permissions(store.root))
    _log.info(
        "postcall: queue=%s media=%s retention=%dd",
        args.queue,
        media_dir(settings),
        settings.recording_retention_days,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    deps = WorkerDeps(
        queue=queue,
        store=store,
        media_root=media_dir(settings),
        consent_ok=lambda: consent_signed_off(CONSENT_LINE),
        retention_days=settings.recording_retention_days,
        spool=spool_for_drain,
    )

    try:
        stats = await run_worker(deps, stop=stop, drain=args.drain)
    finally:
        aclose = getattr(queue, "aclose", None)
        if aclose is not None:
            await aclose()

    _log.info(
        "postcall: exiting — processed=%d stored=%d consent_blocked=%d retried=%d "
        "dead=%d pruned_days=%d",
        stats.processed,
        stats.stored,
        stats.blocked_by_consent,
        stats.retried,
        stats.dead_lettered,
        stats.pruned_days,
    )
    return 0


def main(argv=None) -> int:
    configure_logging()
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
