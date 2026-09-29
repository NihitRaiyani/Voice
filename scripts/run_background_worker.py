"""Consume durable post-call jobs without starting the audio pipeline.

uv run python scripts/run_background_worker.py
uv run python scripts/run_background_worker.py --drain
"""

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
from roma.repositories.postgres.background_jobs import BackgroundJobStore
from roma.workers.background import run_background_worker

_log = logging.getLogger("roma.workers.background")


async def _run(*, drain: bool) -> int:
    settings = get_settings()
    if not settings.database_url.get_secret_value():
        raise RuntimeError("DATABASE_URL is required for background jobs")
    database = Database.from_settings(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        count = await run_background_worker(
            BackgroundJobStore(database.session_factory), stop=stop, drain=drain
        )
        _log.info("background worker stopped after %d job(s)", count)
    finally:
        await database.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Roma durable background-job worker")
    parser.add_argument("--drain", action="store_true", help="Process due jobs, then exit")
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(_run(drain=args.drain))


if __name__ == "__main__":
    raise SystemExit(main())
