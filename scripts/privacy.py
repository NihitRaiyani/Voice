"""Preview managed call disposal or selected-field backfill; --apply is explicit.

No default command mutates records. Run against the operator-selected DATABASE_URL;
this script never generates keys, migrates a database, or edits private settings.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redis import asyncio as redis_asyncio
from roma.core.config import get_settings
from roma.core.database import Database
from roma.core.field_encryption import FieldCipher, configure_field_encryption
from roma.core.privacy_settings import PrivacySettings
from roma.repositories.postgres.models import Caller, CallTurn
from roma.repositories.postgres.privacy import SQLPrivacyRepository
from roma.services.privacy_service import PrivacyService
from sqlalchemy import Text, cast, select, update


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Preview/apply managed privacy operations")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--call-id", type=UUID)
    operation.add_argument("--expired", action="store_true")
    operation.add_argument("--backfill-encryption", action="store_true")
    parser.add_argument("--action", choices=["delete", "anonymize"], default="delete")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args(argv)


async def backfill(factory, *, apply, limit, settings):
    if apply and not settings.pii_encryption_enabled:
        raise ValueError("Explicit field encryption activation is required for backfill.")
    counts = {}
    async with factory() as session, session.begin():
        for model, column in ((Caller, Caller.name), (CallTurn, CallTurn.transcript)):
            live = (
                Caller.anonymized_at.is_(None)
                if model is Caller
                else CallTurn.transcript_deleted_at.is_(None)
            )
            statement = (
                select(model.id, column)
                .where(
                    live,
                    column.is_not(None),
                    ~cast(column, Text).startswith(FieldCipher.prefix),
                )
                .limit(limit)
            )
            if apply:
                # Disposal updates these same rows. Lock the values before reading
                # them, so its committed erasure can never be overwritten by a stale
                # plaintext snapshot. Busy rows can be handled in a later batch.
                statement = statement.with_for_update(skip_locked=True)
            rows = list(await session.execute(statement))
            counts[model.__tablename__] = len(rows)
            if apply:
                for row_id, value in rows:
                    # Typed UPDATE passes through the authenticated encryption boundary.
                    await session.execute(
                        update(model)
                        .where(model.id == row_id, live, cast(column, Text) == value)
                        .values({column.key: value})
                        .execution_options(synchronize_session=False)
                    )
    return {"applied": apply, "plaintext_rows_in_batch": counts, "batch_limit_per_table": limit}


async def run(args):
    if not 1 <= args.limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000.")
    settings, privacy = get_settings(), PrivacySettings()
    configure_field_encryption(privacy)
    database = Database.from_settings(settings)
    redis = redis_asyncio.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        service = PrivacyService(
            SQLPrivacyRepository(database.session_factory), settings.roma_data_dir, redis=redis
        )
        if args.backfill_encryption:
            return await backfill(
                database.session_factory, apply=args.apply, limit=args.limit, settings=privacy
            )
        if args.expired:
            return await service.retention(apply=args.apply, limit=args.limit)
        return await service.dispose(args.call_id, action=args.action, apply=args.apply)
    finally:
        await redis.aclose()
        await database.close()


def main(argv=None):
    try:
        print(json.dumps(asyncio.run(run(parse_args(argv))), sort_keys=True))
        return 0
    except Exception:  # noqa: BLE001 — CLI prints no secrets or database parameters
        print(
            "Privacy operation failed; inspect configuration and retry. No completion is claimed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
