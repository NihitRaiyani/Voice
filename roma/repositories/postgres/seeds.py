"""Idempotent synthetic reference data; never imported by schema migrations."""

from uuid import NAMESPACE_URL, UUID, uuid5

from alembic.runtime.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roma.repositories.postgres.models import Branch, BranchCourse, Course, Institute, Role


class DemoSeedError(RuntimeError):
    """Safe operator error with no connection details or business values."""


def _id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"urn:roma:demo:v1:{name}")


async def seed_demo(
    session_factory: async_sessionmaker[AsyncSession], *, expected_head: str, app_env: str
) -> int:
    """Insert one atomic demo profile without replacing existing data."""
    if app_env not in {"dev", "test"}:
        raise DemoSeedError("Demo data requires APP_ENV=dev or test")
    inserted = 0
    async with session_factory() as session, session.begin():
        connection = await session.connection()
        heads = await connection.run_sync(
            lambda c: MigrationContext.configure(c).get_current_heads()
        )
        if heads != (expected_head,):
            raise DemoSeedError("Apply the current Alembic head before demo seeding")

        rows = [
            (
                Institute,
                dict(
                    id=_id("institute"),
                    code="roma-demo",
                    name="Roma Demo Institute",
                    is_active=False,
                ),
            ),
            (
                Branch,
                dict(
                    id=_id("branch"),
                    institute_id=_id("institute"),
                    code="demo-campus",
                    name="Demo Campus",
                    city="Demo City",
                    timezone="Asia/Kolkata",
                    is_active=False,
                ),
            ),
            (
                Course,
                dict(
                    id=_id("course"),
                    institute_id=_id("institute"),
                    code="demo-course",
                    name="Demo Course",
                    description="Synthetic demo; not approved counselling content.",
                    is_active=False,
                ),
            ),
        ]
        for model, values in rows:
            result = await session.execute(
                insert(model).values(**values).on_conflict_do_nothing().returning(model.id)
            )
            inserted += result.scalar_one_or_none() is not None
            existing = await session.get(model, values["id"])
            # An existing natural key with a different ID must not attach demos
            # to someone else's business records. Existing names are not rewritten.
            identity = {k: v for k, v in values.items() if k in {"code", "institute_id"}}
            if existing is None or any(getattr(existing, k) != v for k, v in identity.items()):
                raise DemoSeedError("Demo namespace conflicts with existing reference data")

        result = await session.execute(
            insert(BranchCourse)
            .values(branch_id=_id("branch"), course_id=_id("course"))
            .on_conflict_do_nothing()
            .returning(BranchCourse.branch_id)
        )
        inserted += result.scalar_one_or_none() is not None
        for name in ("admin", "counsellor", "viewer"):
            result = await session.execute(
                insert(Role)
                .values(
                    id=_id(f"role:{name}"),
                    name=name,
                    description="Role vocabulary; no demo account or permissions",
                )
                .on_conflict_do_nothing()
                .returning(Role.id)
            )
            inserted += result.scalar_one_or_none() is not None
            if await session.scalar(select(Role.id).where(Role.name == name)) is None:
                raise DemoSeedError("Demo role identity conflicts with existing reference data")
    return inserted
