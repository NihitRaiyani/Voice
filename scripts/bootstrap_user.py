"""Explicit operator-only user provisioning; no default account, password or role.

Example: uv run python scripts/bootstrap_user.py --email teacher@example.invalid \\
    --display-name Teacher --role admin
Passwords are prompted without echo. Automation may explicitly select --password-stdin.
This command never runs migrations, seeds demo data or prints credentials.
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roma.core.migration_settings import MigrationSettings
from roma.core.security import Passwords
from roma.repositories.postgres.auth import AuthRepository
from roma.repositories.postgres.unit_of_work import AsyncpgAvailabilityError
from roma.services.auth_service import (
    ROLE_PERMISSIONS,
    AuthError,
    normalize_email,
    validate_roles,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def create_user(factory, *, email, display_name, password, roles):
    email = normalize_email(email)
    validate_roles(roles)
    if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 255:
        raise ValueError("Display name must contain 1..255 characters")
    encoded = await asyncio.to_thread(Passwords().hash, password)
    async with asyncio.timeout(3):
        user = await AuthRepository(factory).create_user(
            email, display_name.strip(), encoded, roles
        )
    if user is None:
        raise AuthError(409, "USER_CONFLICT", "User already exists.")
    return user


async def _run(args, password):
    engine = create_async_engine(
        MigrationSettings().require_url(),
        echo=False,
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=3,
        connect_args={"timeout": 3, "command_timeout": 3},
    )
    try:
        user = await create_user(
            async_sessionmaker(engine, expire_on_commit=False),
            email=args.email,
            display_name=args.display_name,
            password=password,
            roles=set(args.role),
        )
        print(f"Created user {user.user_id} with roles {', '.join(sorted(user.roles))}.")
    finally:
        await engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--role", required=True, action="append", choices=sorted(ROLE_PERMISSIONS)
    )
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args(argv)
    if args.password_stdin:
        password = sys.stdin.readline(1026).removesuffix("\n").removesuffix("\r")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Confirm password: ") != password:
            print("Passwords do not match.", file=sys.stderr)
            return 1
    try:
        asyncio.run(_run(args, password))
    except (
        AuthError,
        ValueError,
        RuntimeError,
        SQLAlchemyError,
        OSError,
        TimeoutError,
        *AsyncpgAvailabilityError,
    ):
        print(
            "User provisioning failed; verify input, migrated schema and database availability.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
