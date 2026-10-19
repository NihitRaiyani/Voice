"""Explicitly save an already completed ASR/TTS report using database-only settings.

No inference, model loading, downloads, migrations or provider requests occur.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.migration_settings import MigrationSettings  # noqa: E402
from roma.eval.benchmark_evidence import evidence_from_report, read_report  # noqa: E402
from roma.repositories.postgres.benchmarks import PostgresBenchmarkStore  # noqa: E402
from roma.repositories.postgres.models import BenchmarkResult, BenchmarkRun  # noqa: E402


async def run(args: argparse.Namespace) -> dict:
    if not args.allow_database_write:
        raise ValueError("Benchmark persistence requires --allow-database-write")
    report, checksum = read_report(args.report)
    evidence = evidence_from_report(
        report, run_key=args.run_key, report_path=args.report, report_sha256=checksum
    )
    settings = MigrationSettings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    engine = create_async_engine(
        settings.require_url(),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"timeout": 10, "command_timeout": 15},
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        saved = await PostgresBenchmarkStore(factory).save(evidence)
        async with factory() as session:
            stored = await session.get(BenchmarkRun, saved.run_id)
            if stored is None:
                raise RuntimeError("Saved benchmark is unavailable during readback")
            results = await session.scalar(
                select(func.count())
                .select_from(BenchmarkResult)
                .where(BenchmarkResult.run_id == saved.run_id)
            )
            return {
                "run_id": str(saved.run_id),
                "created": saved.created,
                "status": stored.status,
                "sample_count": stored.sample_count,
                "result_rows": results,
                "model_identities": len(evidence.models),
                "report_sha256": checksum,
            }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--allow-database-write", action="store_true", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), sort_keys=True))
        return 0
    except Exception:  # noqa: BLE001 -- SQL/validation errors may contain credentials or private data.
        print(
            json.dumps(
                {
                    "error": "Benchmark save failed; verify completed report, run identity and database availability"
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
