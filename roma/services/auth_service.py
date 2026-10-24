"""Human authentication: live SQL authorization, opaque refresh rotation and revocation."""

import asyncio
import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from roma.core.security import AccessTokens, Passwords
from roma.repositories.postgres.auth import AuthRepository
from roma.repositories.postgres.unit_of_work import AsyncpgAvailabilityError


class Permission(StrEnum):
    CALLS_VIEW = "calls.view"
    CALLS_CREATE = "calls.create"
    ANALYTICS_VIEW = "analytics.view"
    RECORDINGS_VIEW = "recordings.view"
    APPOINTMENTS_VIEW = "appointments.view"
    APPOINTMENTS_MANAGE = "appointments.manage"
    LEADS_VIEW = "leads.view"
    SAFETY_VIEW = "safety.view"
    SAFETY_MANAGE = "safety.manage"
    USERS_MANAGE = "users.manage"
    RAG_DEBUG = "rag.debug"
    RAG_UPLOAD = "rag.upload"
    RAG_APPROVE = "rag.approve"


_COMMON = frozenset({Permission.CALLS_VIEW, Permission.ANALYTICS_VIEW, Permission.SAFETY_VIEW})
ROLE_PERMISSIONS = {
    "viewer": _COMMON,
    "counsellor": _COMMON
    | {
        Permission.RECORDINGS_VIEW,
        Permission.APPOINTMENTS_VIEW,
        Permission.APPOINTMENTS_MANAGE,
        Permission.LEADS_VIEW,
        Permission.RAG_DEBUG,
    },
    "admin": frozenset(Permission),
}


class AuthError(RuntimeError):
    def __init__(
        self, status_code=401, code="UNAUTHORIZED", message="Invalid or expired credentials."
    ):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    display_name: str
    roles: frozenset[str]
    is_active: bool = True

    @property
    def permissions(self):
        return frozenset(
            permission for role in self.roles for permission in ROLE_PERMISSIONS.get(role, ())
        )

    def require(self, permission):
        if permission not in self.permissions:
            raise AuthError(403, "FORBIDDEN", "Permission denied.")
        return self


@dataclass(frozen=True)
class TokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    token_type: str = "bearer"


def normalize_email(value):
    if (
        not isinstance(value, str)
        or not value.isascii()
        or len(value) > 320
        or not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,253}", value)
    ):
        raise AuthError(422, "VALIDATION_ERROR", "Invalid email.")
    return value.casefold()


def validate_roles(roles):
    if (
        not isinstance(roles, (set, frozenset))
        or not roles
        or not roles <= ROLE_PERMISSIONS.keys()
    ):
        raise AuthError(422, "VALIDATION_ERROR", "Choose known roles.")
    return roles


def refresh_hash(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{64}", token):
        raise AuthError()
    return sha256(token.encode("ascii")).hexdigest()


def _principal(user):
    return Principal(user.user_id, user.email, user.display_name, user.roles, user.is_active)


class AuthService:
    def __init__(self, session_factory, settings, *, repository=None):
        self.tokens = AccessTokens(settings)
        self.settings = settings
        self.repository = repository or AuthRepository(session_factory)
        self.passwords = Passwords()
        self._password_slots = asyncio.Semaphore(2)
        self._password_work = set()
        self._dummy = self.passwords.hash(token_urlsafe(32))

    async def _password(self, operation, *args):
        await self._password_slots.acquire()
        work = asyncio.create_task(asyncio.to_thread(operation, *args))
        self._password_work.add(work)

        def completed(task):
            self._password_work.discard(task)
            self._password_slots.release()
            if not task.cancelled():
                task.exception()

        work.add_done_callback(completed)
        # Request cancellation cannot cancel native hashing or free its admission slot.
        return await asyncio.shield(work)

    async def aclose(self):
        if self._password_work:
            await asyncio.gather(*self._password_work, return_exceptions=True)

    async def _database(self, operation):
        try:
            async with asyncio.timeout(3):
                return await operation
        except (SQLAlchemyError, OSError, TimeoutError, *AsyncpgAvailabilityError):
            raise AuthError(
                503, "AUTH_UNAVAILABLE", "Authentication storage is unavailable."
            ) from None

    def _pair(self, user_id, sid, refresh):
        return TokenPair(
            self.tokens.issue(user_id, sid), refresh, self.settings.auth_access_ttl_secs
        )

    async def login(self, email, password):
        try:
            email = normalize_email(email)
        except AuthError:
            raise AuthError() from None
        user = await self._database(self.repository.find_user(email))
        verified = await self._password(
            self.passwords.verify, user.password_hash if user else self._dummy, password
        )
        if user is None or not user.is_active or not verified:
            raise AuthError()
        refresh = token_urlsafe(48)
        sid = await self._database(
            self.repository.start(
                user, refresh_hash(refresh), self.settings.auth_refresh_ttl_secs
            )
        )
        if sid is None:
            raise AuthError()
        return self._pair(user.user_id, sid, refresh)

    async def refresh(self, token):
        hashed = refresh_hash(token)
        successor = token_urlsafe(48)
        result = await self._database(self.repository.rotate(hashed, refresh_hash(successor)))
        if result is None:
            raise AuthError()
        return self._pair(*result, successor)

    async def logout(self, token):
        await self._database(self.repository.revoke(refresh_hash(token)))

    async def authenticate(self, token):
        try:
            claims = self.tokens.decode(token)
        except ValueError:
            raise AuthError() from None
        user = await self._database(
            self.repository.principal(UUID(claims["sub"]), UUID(claims["sid"]))
        )
        if user is None:
            raise AuthError()
        return _principal(user)

    async def create_user(self, email, display_name, password, roles):
        email = normalize_email(email)
        validate_roles(roles)
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 255:
            raise AuthError(422, "VALIDATION_ERROR", "Invalid display name.")
        try:
            encoded = await self._password(self.passwords.hash, password)
        except ValueError:
            raise AuthError(
                422,
                "VALIDATION_ERROR",
                "Password must contain 12..256 characters and at most 1024 bytes.",
            ) from None
        user = await self._database(
            self.repository.create_user(email, display_name.strip(), encoded, roles)
        )
        if user is None:
            raise AuthError(409, "USER_CONFLICT", "User already exists.")
        return _principal(user)

    async def update_user(self, user_id, *, roles=None, is_active=None):
        if roles is not None:
            validate_roles(roles)
        if is_active is not None and type(is_active) is not bool:
            raise AuthError(422, "VALIDATION_ERROR", "Invalid active flag.")
        if roles is None and is_active is None:
            raise AuthError(422, "VALIDATION_ERROR", "No changes supplied.")
        user = await self._database(self.repository.update_user(user_id, roles, is_active))
        if user is None:
            raise AuthError(404, "USER_NOT_FOUND", "User does not exist.")
        return _principal(user)
