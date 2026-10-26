"""Knowledge worker boundary. Imports never connect SQL/Redis or load a model."""

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from roma.providers.jobs.knowledge import ACTOR_NAME, QUEUE_NAME

_log = logging.getLogger("roma.workers.knowledge")


async def process_knowledge_job(service, job_id: str | UUID) -> str:
    parsed = job_id if isinstance(job_id, UUID) else UUID(job_id)
    return await service.process(parsed)


async def run_pending(service, repository, *, limit: int = 100) -> int:
    """Drain one bounded page; SQL claim/fencing owns duplicates and crash recovery."""
    if not 1 <= limit <= 1000:
        raise ValueError("Invalid knowledge drain limit")
    ids = await repository.dispatchable_ids(datetime.now(UTC), limit=limit)
    for job_id in ids:
        await process_knowledge_job(service, job_id)
    return len(ids)


def create_knowledge_actor(broker, service_factory):
    import dramatiq

    @dramatiq.actor(broker=broker, actor_name=ACTOR_NAME, queue_name=QUEUE_NAME, priority=100)
    async def consume(job_id: str):
        try:
            parsed = UUID(job_id)
        except (ValueError, TypeError, AttributeError):
            _log.warning("discarding invalid knowledge job ID")
            return
        try:
            await process_knowledge_job(service_factory(), parsed)
        except Exception:  # noqa: BLE001 — framework logs cannot retain source/provider error bodies
            raise RuntimeError("Knowledge delivery unavailable") from None

    return consume


async def run_database_drain(*, limit: int) -> int:
    """Explicit operator composition; never migrates, seeds or changes approval state."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from roma.core.migration_settings import MigrationSettings
    from roma.core.rag_settings import KnowledgeRuntimeSettings
    from roma.services.knowledge_runtime import build_knowledge_components

    settings = KnowledgeRuntimeSettings()
    database = MigrationSettings()
    engine = create_async_engine(database.require_url(), pool_size=1, max_overflow=0)
    components = None
    try:
        components = build_knowledge_components(
            async_sessionmaker(engine, expire_on_commit=False), settings
        )
        return await run_pending(
            components.ingestion, components.ingestion_repository, limit=limit
        )
    finally:
        try:
            close = getattr(components.provider, "aclose", None) if components else None
            if close is not None:
                await close()
        finally:
            await engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Drain bounded SQL knowledge ingestion jobs.")
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--allow-database-write", action="store_true")
    args = parser.parse_args(argv)
    if not args.drain or not args.allow_database_write:
        parser.error("--drain and --allow-database-write are required")
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    try:
        processed = asyncio.run(run_database_drain(limit=args.limit))
    except Exception:  # noqa: BLE001 — config/provider errors must not expose private connection data
        print("Knowledge worker unavailable; check configuration and durable job status.")
        return 1
    print(f"knowledge_jobs_processed={processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
