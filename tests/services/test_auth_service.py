import asyncio

import pytest
from pydantic import SecretStr
from roma.core.auth_settings import AuthSettings
from roma.services.auth_service import AuthError, AuthService, Permission
from tests.repositories.postgres.test_repositories import (
    alembic_config,
    disposable_postgres_url,
    session_factory,
)

__all__ = ["session_factory", "alembic_config", "disposable_postgres_url"]
pytestmark = pytest.mark.postgres


def settings():
    return AuthSettings(_env_file=None, auth_jwt_secret=SecretStr("synthetic-only-key-" * 3))


def test_login_refresh_rotation_reuse_revokes_access_and_logout_is_durable(session_factory):
    async def run():
        service = AuthService(session_factory, settings())
        user = await service.create_user(
            "learner@example.invalid", "Synthetic admin", "synthetic password 123!", {"admin"}
        )
        with pytest.raises(AuthError) as bad:
            await service.login("learner@example.invalid", "incorrect password")
        assert bad.value.status_code == 401
        first = await service.login("learner@example.invalid", "synthetic password 123!")
        principal = await service.authenticate(first.access_token)
        assert (
            principal.user_id == user.user_id
            and Permission.USERS_MANAGE in principal.permissions
        )
        second = await service.refresh(first.refresh_token)
        assert second.refresh_token != first.refresh_token
        assert (await service.authenticate(second.access_token)).user_id == user.user_id
        with pytest.raises(AuthError):
            await service.refresh(first.refresh_token)
        with pytest.raises(AuthError):
            await service.authenticate(second.access_token)
        with pytest.raises(AuthError):
            await service.refresh(second.refresh_token)
        third = await service.login("learner@example.invalid", "synthetic password 123!")
        await service.logout(third.refresh_token)
        with pytest.raises(AuthError):
            await service.authenticate(third.access_token)
        await service.logout(third.refresh_token)

    asyncio.run(run())


def test_role_edits_and_disable_are_live_and_reactivation_does_not_restore_sessions(
    session_factory,
):
    async def run():
        service = AuthService(session_factory, settings())
        user = await service.create_user(
            "viewer@example.invalid", "Synthetic viewer", "synthetic password 123!", {"viewer"}
        )
        pair = await service.login(user.email, "synthetic password 123!")
        principal = await service.authenticate(pair.access_token)
        principal.require("calls.view")
        with pytest.raises(AuthError) as denied:
            principal.require("recordings.view")
        assert denied.value.status_code == 403
        await service.update_user(user.user_id, roles={"counsellor"})
        (await service.authenticate(pair.access_token)).require("recordings.view")
        with pytest.raises(AuthError):
            (await service.authenticate(pair.access_token)).require("users.manage")
        await service.update_user(user.user_id, is_active=False)
        with pytest.raises(AuthError):
            await service.authenticate(pair.access_token)
        await service.update_user(user.user_id, is_active=True)
        with pytest.raises(AuthError):
            await service.refresh(pair.refresh_token)
        with pytest.raises(AuthError):
            await service.authenticate(pair.access_token)

    asyncio.run(run())


def test_concurrent_refresh_has_one_rotation_and_reuse_revokes_the_family(session_factory):
    async def run():
        service = AuthService(session_factory, settings())
        await service.create_user(
            "race@example.invalid", "Synthetic race", "synthetic password 123!", {"viewer"}
        )
        pair = await service.login("race@example.invalid", "synthetic password 123!")
        results = await asyncio.gather(
            service.refresh(pair.refresh_token),
            service.refresh(pair.refresh_token),
            return_exceptions=True,
        )
        assert sum(isinstance(row, AuthError) for row in results) == 1
        winner = next(row for row in results if not isinstance(row, AuthError))
        with pytest.raises(AuthError):
            await service.authenticate(winner.access_token)

    asyncio.run(run())


def test_refresh_storage_contains_only_hashes_and_expired_sessions_fail(session_factory):
    from datetime import UTC, datetime, timedelta

    from roma.repositories.postgres.models import AuthRefreshToken, AuthSession
    from roma.services.auth_service import refresh_hash
    from sqlalchemy import select

    async def run():
        service = AuthService(session_factory, settings())
        await service.create_user(
            "expiry@example.invalid", "Synthetic expiry", "synthetic password 123!", {"viewer"}
        )
        pair = await service.login("expiry@example.invalid", "synthetic password 123!")
        async with session_factory() as session, session.begin():
            token = (await session.scalars(select(AuthRefreshToken))).one()
            assert (
                token.token_hash == refresh_hash(pair.refresh_token)
                and token.token_hash != pair.refresh_token
            )
            assert pair.refresh_token not in repr(token.__dict__)
            family = await session.get(AuthSession, token.session_id)
            family.created_at = datetime.now(UTC) - timedelta(hours=1)
            family.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(AuthError):
            await service.authenticate(pair.access_token)
        with pytest.raises(AuthError):
            await service.refresh(pair.refresh_token)

    asyncio.run(run())


def test_repeated_request_cancellation_keeps_password_work_bounded():
    import threading

    async def run():
        service = AuthService(None, settings(), repository=object())
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        calls = []

        def blocked():
            calls.append(True)
            loop.call_soon_threadsafe(entered.set)
            release.wait(3)

        first = asyncio.create_task(service._password(blocked))
        await entered.wait()
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(service._password(blocked))
        third = asyncio.create_task(service._password(blocked))
        await asyncio.sleep(0.02)
        try:
            assert len(calls) <= 2
        finally:
            release.set()
            await asyncio.gather(second, third)

    asyncio.run(run())
