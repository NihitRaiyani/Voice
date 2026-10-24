"""Short SQL transactions own users, live sessions and serialized refresh rotation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from roma.repositories.postgres.models import (
    AuthRefreshToken,
    AuthSession,
    Role,
    User,
    UserRole,
)


@dataclass(frozen=True)
class AuthUser:
    user_id: UUID
    email: str
    display_name: str
    roles: frozenset[str]
    is_active: bool
    password_hash: str = field(repr=False)


class AuthRepository:
    def __init__(self, session_factory):
        self.factory = session_factory

    async def _user(self, session, user):
        roles = await session.scalars(
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
        )
        return AuthUser(
            user.id,
            user.email,
            user.display_name,
            frozenset(roles),
            user.is_active,
            user.password_hash,
        )

    async def find_user(self, email):
        async with self.factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            return await self._user(session, user) if user else None

    async def start(self, expected_user, token_hash, lifetime):
        now = datetime.now(UTC)
        async with self.factory() as session, session.begin():
            user = await session.scalar(
                select(User).where(User.id == expected_user.user_id).with_for_update()
            )
            if (
                user is None
                or not user.is_active
                or user.password_hash != expected_user.password_hash
            ):
                return None
            sid = uuid4()
            expires = now + timedelta(seconds=lifetime)
            session.add(
                AuthSession(id=sid, user_id=user.id, created_at=now, expires_at=expires)
            )
            await session.flush()  # Explicit order: the refresh FK references the session.
            session.add(
                AuthRefreshToken(
                    id=uuid4(),
                    session_id=sid,
                    token_hash=token_hash,
                    created_at=now,
                    expires_at=expires,
                )
            )
            return sid

    async def rotate(self, token_hash, successor_hash):
        now = datetime.now(UTC)
        async with self.factory() as session, session.begin():
            # Lock order is session then token for every operation on this family.
            sid = await session.scalar(
                select(AuthRefreshToken.session_id).where(
                    AuthRefreshToken.token_hash == token_hash
                )
            )
            if sid is None:
                return None
            family = await session.scalar(
                select(AuthSession).where(AuthSession.id == sid).with_for_update()
            )
            token = await session.scalar(
                select(AuthRefreshToken)
                .where(AuthRefreshToken.token_hash == token_hash)
                .with_for_update()
            )
            if family is None or token is None or family.revoked_at is not None:
                return None
            if token.consumed_at is not None:
                family.revoked_at = now  # Commit reuse revocation before reporting denial.
                return None
            user = await session.get(User, family.user_id)
            if (
                family.expires_at <= now
                or token.expires_at <= now
                or user is None
                or not user.is_active
            ):
                family.revoked_at = now
                return None
            token.consumed_at = now
            session.add(
                AuthRefreshToken(
                    id=uuid4(),
                    session_id=sid,
                    token_hash=successor_hash,
                    created_at=now,
                    expires_at=family.expires_at,
                )
            )
            return family.user_id, family.id

    async def revoke(self, token_hash):
        now = datetime.now(UTC)
        async with self.factory() as session, session.begin():
            sid = await session.scalar(
                select(AuthRefreshToken.session_id).where(
                    AuthRefreshToken.token_hash == token_hash
                )
            )
            if sid is not None:
                family = await session.scalar(
                    select(AuthSession).where(AuthSession.id == sid).with_for_update()
                )
                if family is not None and family.revoked_at is None:
                    family.revoked_at = now

    async def principal(self, user_id, sid):
        async with self.factory() as session:
            user = await session.scalar(
                select(User)
                .join(AuthSession, AuthSession.user_id == User.id)
                .where(
                    User.id == user_id,
                    User.is_active.is_(True),
                    AuthSession.id == sid,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > datetime.now(UTC),
                )
            )
            return await self._user(session, user) if user else None

    async def _assign(self, session, user_id, roles):
        await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role in sorted(roles):
            role_id = await session.scalar(
                insert(Role)
                .values(id=uuid4(), name=role)
                .on_conflict_do_update(index_elements=[Role.name], set_={"name": role})
                .returning(Role.id)
            )
            session.add(UserRole(user_id=user_id, role_id=role_id))
        await session.flush()

    async def create_user(self, email, display_name, encoded, roles):
        async with self.factory() as session, session.begin():
            user_id = await session.scalar(
                insert(User)
                .values(
                    id=uuid4(),
                    email=email,
                    display_name=display_name,
                    password_hash=encoded,
                    is_active=True,
                )
                .on_conflict_do_nothing(index_elements=[User.email])
                .returning(User.id)
            )
            if user_id is None:
                return None
            await self._assign(session, user_id, roles)
            return await self._user(session, await session.get(User, user_id))

    async def update_user(self, user_id, roles=None, is_active=None):
        async with self.factory() as session, session.begin():
            user = await session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None:
                return None
            if roles is not None:
                await self._assign(session, user_id, roles)
            if is_active is not None:
                user.is_active = is_active
                if not is_active:
                    # Revocation survives a later reactivation of the same account.
                    families = await session.scalars(
                        select(AuthSession)
                        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                        .with_for_update()
                    )
                    for family in families:
                        family.revoked_at = datetime.now(UTC)
            await session.flush()
            return await self._user(session, user)
