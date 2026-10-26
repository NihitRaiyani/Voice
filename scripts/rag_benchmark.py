#!/usr/bin/env python3
"""Synthetic RAG regression lab; local mode writes only to an explicit TEST ONLY DB."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.eval.rag_benchmark import (  # noqa: E402
    benchmark_evidence,
    guard_test_database_url,
    load_golden,
    run_local_benchmark,
    run_mock_benchmark,
)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=("mock", "local"), default="mock")
    result.add_argument("--golden", type=Path, default=ROOT / "benchmarks/rag/golden-v1.json")
    result.add_argument(
        "--report", type=Path, default=ROOT / "var/roma/rag-benchmark/report.json"
    )
    result.add_argument(
        "--embedding-manifest",
        type=Path,
        default=ROOT / "var/roma/models/multilingual-e5-small/manifest.json",
    )
    result.add_argument(
        "--test-only",
        action="store_true",
        help="select a fresh migrated loopback roma_rag_benchmark_ database",
    )
    result.add_argument(
        "--persist",
        action="store_true",
        help="save aggregate evidence in the same TEST ONLY database",
    )
    result.add_argument(
        "--no-calibrate",
        action="store_true",
        help="use the declared .80 default without calibration",
    )
    result.add_argument(
        "--check",
        action="store_true",
        help="fail on mechanical target failures; never implies a quality gate",
    )
    return result


async def run(args):
    golden = load_golden(args.golden)
    factory, engine = None, None
    try:
        if args.mode == "mock":
            if args.persist:
                raise ValueError("Mock fixture mode does not persist to a database")
            report = await run_mock_benchmark(golden)
        else:
            from roma.providers.ai.embeddings import LocalMultilingualEmbedding
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            url = guard_test_database_url(
                os.environ.get("RAG_BENCHMARK_DATABASE_URL", ""), test_only=args.test_only
            )
            engine = create_async_engine(url, hide_parameters=True, pool_size=2, max_overflow=0)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            provider = LocalMultilingualEmbedding(
                manifest_path=args.embedding_manifest,
                timeout_secs=60,
                max_batch_size=4,
                cpu_threads=2,
            )
            report = await run_local_benchmark(
                golden, factory, provider, calibrate=not args.no_calibrate
            )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n"
        )
        temporary.replace(args.report)
        persisted_run = None
        if args.persist:
            from roma.repositories.postgres.benchmarks import PostgresBenchmarkStore

            result = await PostgresBenchmarkStore(factory).save(
                benchmark_evidence(report, args.report)
            )
            persisted_run = str(result.run_id)
        print(
            json.dumps(
                {
                    "report": str(args.report.resolve()),
                    "complete": report["complete"],
                    "mode": args.mode,
                    "summary": report["summary"],
                    "mechanical_checks_passed": report["mechanical_checks_passed"],
                    "quality_gate_passed": False,
                    "persisted_run_id": persisted_run,
                },
                sort_keys=True,
            )
        )
        return 2 if args.check and not report["mechanical_checks_passed"] else 0
    finally:
        if engine is not None:
            await engine.dispose()


def main():
    args = parser().parse_args()
    try:
        return asyncio.run(run(args))
    except Exception:  # noqa: BLE001 — never print URLs, provider/SQL bodies or private environment.
        print(
            json.dumps(
                {
                    "error": "rag-benchmark-unavailable",
                    "details": "Require checked synthetic fixtures and, for local mode, a fresh migrated TEST ONLY database and pinned local embedding manifest.",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
