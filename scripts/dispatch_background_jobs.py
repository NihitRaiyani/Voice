"""Publish ready PostgreSQL job IDs to Dramatiq; --once publishes one bounded batch."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roma.core.config import get_settings
from roma.core.database import Database
from roma.core.logging import configure_logging
from roma.providers.jobs.dramatiq import DramatiqPublisher, build_broker
from roma.repositories.postgres.background_jobs import BackgroundJobStore
from roma.services.job_dispatcher import dispatch_due_jobs

_log = logging.getLogger("roma.workers.dispatcher")


async def _run(*, once: bool) -> int:
    settings = get_settings()
    if not settings.database_url.get_secret_value():
        raise RuntimeError("DATABASE_URL is required for background jobs")
    database = Database.from_settings(settings)
    publisher = DramatiqPublisher(build_broker(settings.redis_url.get_secret_value()))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        store = BackgroundJobStore(database.session_factory)
        while not stop.is_set():
            try:
                count = await dispatch_due_jobs(store, publisher)
                if count:
                    _log.info("published %d durable background job IDs", count)
            except Exception as exc:  # noqa: BLE001 — intents remain ready during outages
                _log.error("job dispatch unavailable (%s)", type(exc).__name__)
                if once:
                    return 1
            if once:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
    finally:
        try:
            await publisher.close()
        finally:
            await database.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Roma PostgreSQL-to-Dramatiq dispatcher")
    parser.add_argument("--once", action="store_true", help="Publish one batch and exit")
    args = parser.parse_args(argv)
    configure_logging()
    return asyncio.run(_run(once=args.once))


if __name__ == "__main__":
    raise SystemExit(main())
