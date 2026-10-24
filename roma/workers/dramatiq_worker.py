"""Framework consumer composition; domain/services do not import Dramatiq."""

import logging
from uuid import UUID

import dramatiq
from dramatiq.asyncio import get_event_loop_thread
from dramatiq.middleware import Middleware

from roma.core.config import get_settings
from roma.core.database import Database
from roma.core.logging import configure_logging
from roma.providers.jobs.dramatiq import (
    ACTOR_NAME,
    QUEUE_NAME,
    SUMMARY_ACTOR_NAME,
    SUMMARY_QUEUE_NAME,
    build_broker,
)
from roma.repositories.postgres.background_jobs import BackgroundJobStore
from roma.workers.background import BackgroundHandlers, process_background_job

_log = logging.getLogger("roma.workers.dramatiq")
_database: Database | None = None


def _store() -> BackgroundJobStore:
    global _database
    if _database is None:
        # Run on the worker's AsyncIO loop; never open asyncpg connections pre-fork.
        _database = Database.from_settings(get_settings())
    return BackgroundJobStore(_database.session_factory)


class DatabaseLifecycle(Middleware):
    def after_worker_shutdown(self, broker, worker):
        global _database
        if _database is not None:
            # Reverse-order hooks close the pool before AsyncIO stops its loop.
            get_event_loop_thread().run_coroutine(_database.close())
            _database = None


def create_background_actor(broker, store_factory, *, handlers=None):
    async def execute(job_id: str) -> None:
        try:
            parsed_id = UUID(job_id)
        except ValueError:
            _log.warning("discarding invalid background job ID")
            return
        try:
            await process_background_job(store_factory(), parsed_id, handlers=handlers)
        except Exception as exc:  # noqa: BLE001 — sanitize framework traceback logging
            raise RuntimeError(
                f"background delivery unavailable ({type(exc).__name__})"
            ) from None

    @dramatiq.actor(broker=broker, actor_name=ACTOR_NAME, queue_name=QUEUE_NAME, priority=0)
    async def consume(job_id: str) -> None:
        await execute(job_id)

    @dramatiq.actor(
        broker=broker,
        actor_name=SUMMARY_ACTOR_NAME,
        queue_name=SUMMARY_QUEUE_NAME,
        priority=100,
    )
    async def summarize(job_id: str) -> None:
        await execute(job_id)

    return consume


def configure_worker_broker(*, allow_paid_summary=False) -> None:
    """Dramatiq CLI factory; importing this module performs no Redis I/O."""
    settings = get_settings()
    if not settings.database_url.get_secret_value():
        raise RuntimeError("DATABASE_URL is required for background jobs")
    configure_logging(settings)
    broker = build_broker(settings.redis_url.get_secret_value())
    broker.add_middleware(DatabaseLifecycle())
    handlers = None
    if allow_paid_summary is True:
        from roma.core.claude_llm_settings import ClaudeLLMSettings
        from roma.providers.jobs.claude_summary import ClaudeSummary

        handlers = BackgroundHandlers(
            summary=ClaudeSummary(ClaudeLLMSettings(), allow_paid_api=True)
        )
    create_background_actor(broker, _store, handlers=handlers)
    dramatiq.set_broker(broker)


def configure_paid_summary_broker() -> None:
    """Explicit CLI opt-in; importing or starting the ordinary worker never enables AI."""
    configure_worker_broker(allow_paid_summary=True)
