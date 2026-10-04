"""Read-only PostgreSQL capacity preflight and opt-in synthetic pool-pressure check.

Uses the existing engine/session factory. No carrier, AI, business-row writes,
Redis operations or schema changes. See docs/runbook.md for allocation rules.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from time import perf_counter

from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as PoolTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roma.core.config import Settings, get_settings
from roma.core.database import Database


async def check_capacity(database: Database, settings: Settings, headroom: int) -> dict:
    """Verify declared demand against real server capacity, reserving other clients."""
    if headroom < 1:
        raise ValueError("headroom must be positive")
    async with database.session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT current_setting('max_connections')::int AS maximum, "
                    "current_setting('superuser_reserved_connections')::int AS superuser, "
                    "COALESCE(current_setting('reserved_connections', true), '0')::int "
                    "AS reserved"
                )
            )
        ).one()
    available = row.maximum - row.superuser - row.reserved - headroom
    return {
        "max_connections": row.maximum,
        "reserved_connections": row.superuser + row.reserved,
        "other_and_ops_headroom": headroom,
        "available_to_roma": available,
        "pool_processes": settings.database_pool_processes,
        "planned_peak_connections": settings.database_peak_connections,
        "connection_budget": settings.database_connection_budget,
        "safe": settings.database_peak_connections <= available,
    }


async def run_load(
    database: Database, *, concurrency: int, units_per_call: int, audio_wait_ms: float
) -> dict:
    """Simulate independent call tasks releasing the pool before each audio wait."""
    if concurrency < 1 or units_per_call < 1:
        raise ValueError("concurrency and units_per_call must be positive")
    if not math.isfinite(audio_wait_ms) or audio_wait_ms < 0:
        raise ValueError("audio_wait_ms must be finite and nonnegative")
    latencies: list[float] = []
    timeouts = 0
    errors = 0
    in_use = 0
    peak = 0
    pool = database.engine.sync_engine.pool

    def checkout(_connection, _record, _proxy):
        nonlocal in_use, peak
        in_use += 1
        peak = max(peak, in_use)

    def checkin(_connection, _record):
        nonlocal in_use
        in_use -= 1

    async def call() -> None:
        nonlocal timeouts, errors
        for _ in range(units_per_call):
            started = perf_counter()
            try:
                async with database.session_factory() as session:
                    async with session.begin():
                        await session.execute(text("SELECT 1"))
                latencies.append((perf_counter() - started) * 1000)
            except PoolTimeout:
                timeouts += 1
            except Exception:  # noqa: BLE001 — aggregate sanitized diagnostics only
                # Do not disclose connection strings or SQL exception parameters.
                errors += 1
            # This simulates media/inference outside the DB session/transaction.
            await asyncio.sleep(audio_wait_ms / 1000)

    event.listen(pool, "checkout", checkout)
    event.listen(pool, "checkin", checkin)
    started = perf_counter()
    try:
        await asyncio.gather(*(call() for _ in range(concurrency)))
    finally:
        event.remove(pool, "checkout", checkout)
        event.remove(pool, "checkin", checkin)
    elapsed = perf_counter() - started
    ordered = sorted(latencies)

    def percentile(fraction: float) -> float | None:
        if not ordered:
            return None
        return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3)

    return {
        "scope": "single-process synthetic DB units; audio waits are simulated",
        "concurrency": concurrency,
        "units_per_call": units_per_call,
        "audio_wait_ms": audio_wait_ms,
        "completed_units": len(latencies),
        "pool_timeouts": timeouts,
        "errors": errors,
        "peak_checked_out": peak,
        "checked_out_after": pool.checkedout(),
        "unit_p50_ms": percentile(0.50),
        "unit_p95_ms": percentile(0.95),
        "elapsed_secs": round(elapsed, 3),
        "safe": timeouts == 0 and errors == 0 and pool.checkedout() == 0,
    }


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.database_url.get_secret_value():
        print(json.dumps({"error": "DATABASE_URL is required"}))
        return 1
    database = Database.from_settings(settings)
    try:
        report = {"capacity": await check_capacity(database, settings, args.headroom)}
        if not report["capacity"]["safe"]:
            print(json.dumps(report, indent=2))
            return 1
        if args.run_load:
            report["load"] = await run_load(
                database,
                concurrency=args.concurrency,
                units_per_call=args.units_per_call,
                audio_wait_ms=args.audio_wait_ms,
            )
        print(json.dumps(report, indent=2))
        return 0 if report.get("load", {"safe": True})["safe"] else 1
    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headroom", type=int, default=10)
    parser.add_argument("--run-load", action="store_true")
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--units-per-call", type=int, default=3)
    parser.add_argument("--audio-wait-ms", type=float, default=10)
    args = parser.parse_args()
    if args.headroom < 1 or args.concurrency < 1 or args.units_per_call < 1:
        parser.error("headroom, concurrency and units-per-call must be positive")
    if not math.isfinite(args.audio_wait_ms) or args.audio_wait_ms < 0:
        parser.error("audio-wait-ms must be finite and nonnegative")
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 — sanitize CLI failures
        print(json.dumps({"error": "database check failed", "type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
