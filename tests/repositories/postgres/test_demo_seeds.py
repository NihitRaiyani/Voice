"""Separate demo seeds are opt-in, atomic, repeatable and never overwrite data."""

import asyncio
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from roma.repositories.postgres.models import Base, Branch, Course, Institute, Role
from roma.repositories.postgres.seeds import DemoSeedError, seed_demo
from sqlalchemy import func, select
from tests.repositories.postgres.test_migrations import alembic_config
from tests.repositories.postgres.test_migrations import (
    disposable_postgres_url as postgres_fixture,
)
from tests.repositories.postgres.test_repositories import session_factory

__all__ = ["alembic_config", "disposable_postgres_url", "session_factory"]
pytestmark = pytest.mark.postgres
HEAD = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()


async def _counts(factory):
    async with factory() as session:
        return {
            name: await session.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items()
        }


@pytest.fixture(scope="module")
def disposable_postgres_url():
    # Reuse Docker/native provisioning, but release this module's server promptly.
    # Extra session-scoped servers can exhaust macOS shared-memory IDs.
    yield from postgres_fixture.__wrapped__()


def test_migrations_have_no_demo_data_and_seeding_is_repeatable(session_factory):
    async def run():
        assert not any((await _counts(session_factory)).values())
        assert await seed_demo(session_factory, expected_head=HEAD, app_env="test") == 7
        assert await seed_demo(session_factory, expected_head=HEAD, app_env="test") == 0
        counts = await _counts(session_factory)
        assert {k: v for k, v in counts.items() if v} == {
            "institutes": 1,
            "branches": 1,
            "courses": 1,
            "branch_courses": 1,
            "roles": 3,
        }
        async with session_factory() as session:
            for model in (Institute, Branch, Course):
                assert (await session.scalars(select(model))).one().is_active is False

    asyncio.run(run())


def test_existing_role_and_edited_demo_names_are_preserved(session_factory):
    async def run():
        role_id = uuid4()
        async with session_factory() as session, session.begin():
            session.add(Role(id=role_id, name="admin", description="Existing approved role"))
        assert await seed_demo(session_factory, expected_head=HEAD, app_env="test") == 6
        async with session_factory() as session, session.begin():
            institute = (await session.scalars(select(Institute))).one()
            institute.name = "Reviewed lab name"
        assert await seed_demo(session_factory, expected_head=HEAD, app_env="test") == 0
        async with session_factory() as session:
            assert (await session.get(Role, role_id)).description == "Existing approved role"
            assert (await session.scalars(select(Institute))).one().name == "Reviewed lab name"

    asyncio.run(run())


def test_natural_key_collision_rolls_back_the_whole_seed(session_factory):
    async def run():
        async with session_factory() as session, session.begin():
            session.add(Institute(id=uuid4(), code="roma-demo", name="Existing Institute"))
        before = await _counts(session_factory)
        with pytest.raises(DemoSeedError, match="namespace conflicts"):
            await seed_demo(session_factory, expected_head=HEAD, app_env="test")
        assert await _counts(session_factory) == before

    asyncio.run(run())


def test_wrong_schema_head_prevents_seed_writes(session_factory):
    async def run():
        with pytest.raises(DemoSeedError, match="current Alembic head"):
            await seed_demo(session_factory, expected_head="different-head", app_env="test")
        assert not any((await _counts(session_factory)).values())

    asyncio.run(run())


def test_concurrent_seed_invocations_do_not_duplicate_rows(session_factory):
    async def run():
        counts = await asyncio.gather(
            *(seed_demo(session_factory, expected_head=HEAD, app_env="test") for _ in range(2))
        )
        assert sorted(counts) == [0, 7]
        assert sum((await _counts(session_factory)).values()) == 7

    asyncio.run(run())


@pytest.mark.parametrize("environment", ["production", "prod", "staging"])
def test_seed_rejects_non_development_environment_before_opening_session(environment):
    def forbidden_factory():
        raise AssertionError("Must not open a database session")

    with pytest.raises(DemoSeedError, match="APP_ENV"):
        asyncio.run(seed_demo(forbidden_factory, expected_head=HEAD, app_env=environment))


def test_late_role_identity_conflict_rolls_back_earlier_demo_inserts(session_factory):
    from uuid import NAMESPACE_URL, uuid5

    async def run():
        async with session_factory() as session, session.begin():
            session.add(
                Role(
                    id=uuid5(NAMESPACE_URL, "urn:roma:demo:v1:role:admin"),
                    name="existing-other-role",
                    description="Keep this role",
                )
            )
        before = await _counts(session_factory)
        with pytest.raises(DemoSeedError, match="role identity conflicts"):
            await seed_demo(session_factory, expected_head=HEAD, app_env="test")
        assert await _counts(session_factory) == before

    asyncio.run(run())
