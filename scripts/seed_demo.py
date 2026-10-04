"""Opt-in demo reference data after migrations; APP_ENV must be dev/test."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.migration_settings import MigrationSettings  # noqa: E402
from roma.repositories.postgres.seeds import DemoSeedError, seed_demo  # noqa: E402


async def _run() -> int:
    settings = MigrationSettings(_env_file=ROOT / ".env")
    if settings.app_env not in {"dev", "test"}:
        raise DemoSeedError("Demo data requires APP_ENV=dev or test")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise DemoSeedError("No Alembic head is available")
    engine = create_async_engine(
        settings.require_url(), poolclass=NullPool, hide_parameters=True
    )
    try:
        count = await seed_demo(
            async_sessionmaker(engine, expire_on_commit=False),
            expected_head=head,
            app_env=settings.app_env,
        )
        print(json.dumps({"profile": "demo-v1", "inserted_rows": count}))
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo",
        action="store_true",
        required=True,
        help="explicitly select synthetic development data",
    )
    parser.parse_args()
    try:
        return asyncio.run(_run())
    except DemoSeedError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001 — never expose credentials/SQL parameters
        print(json.dumps({"error": "Demo seeding failed", "type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
