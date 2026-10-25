"""Booking orchestration: Redis leases surround, never enter, short SQL units of work."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from roma.core.turn_epoch import require_current_epoch
from roma.domain.appointments.booking import BookingError, HoldOwner, SlotKey
from roma.domain.appointments.timeresolve import (
    IST,
    MAX_DAY_OFFSET,
    VISIT_HOUR_END,
    VISIT_HOUR_START,
    VISIT_MIN_LEAD,
)
from roma.domain.persistence import (
    AppointmentRecord,
    PersistenceConflict,
    PersistenceUnavailable,
    RecordNotFound,
)
from roma.repositories.postgres.unit_of_work import AsyncpgAvailabilityError

_log = logging.getLogger(__name__)
T = TypeVar("T")


class BookingService:
    def __init__(
        self,
        store,
        holds=None,
        *,
        now: Callable[[], datetime] | None = None,
        db_timeout: float = 3.0,
        redis_timeout: float = 1.0,
    ) -> None:
        self.store, self.holds = store, holds
        self.now = now or (lambda: datetime.now(UTC))
        self.db_timeout, self.redis_timeout = db_timeout, redis_timeout

    async def database(self, operation: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(self.db_timeout):
                result = await operation
                require_current_epoch()
                return result
        except PersistenceConflict as exc:
            raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE") from exc
        except RecordNotFound as exc:
            raise BookingError("APPOINTMENT_NOT_FOUND") from exc
        except (
            SQLAlchemyError,
            PersistenceUnavailable,
            OSError,
            TimeoutError,
            *AsyncpgAvailabilityError,
        ) as exc:
            raise BookingError("BOOKING_UNAVAILABLE") from exc

    async def _redis(self, operation: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(self.redis_timeout):
                return await operation
        except (RedisError, OSError, TimeoutError) as exc:
            raise BookingError("HOLD_UNAVAILABLE") from exc

    def eligible(self, key: SlotKey) -> bool:
        if key.start_time.tzinfo is not None:
            return False  # Slot times are branch-local SQL TIME, never offset-bearing.
        instant = datetime.combine(key.appointment_date, key.start_time, IST)
        now = self.now().astimezone(IST)
        return (
            VISIT_HOUR_START <= instant.hour < VISIT_HOUR_END
            and now + VISIT_MIN_LEAD <= instant
            and key.appointment_date <= now.date() + timedelta(days=MAX_DAY_OFFSET)
        )

    async def candidates(self, branch_id: UUID, *, on: date | None = None):
        today = self.now().astimezone(IST).date()
        start, end = (on, on) if on else (today, today + timedelta(days=MAX_DAY_OFFSET))
        records = await self.database(self.store.candidates(branch_id, start, end))
        return [
            r
            for r in records
            if self.eligible(SlotKey(r.branch_id, r.appointment_date, r.start_time))
        ]

    async def available(self, key: SlotKey) -> bool:
        return self.eligible(key) and await self.database(self.store.available(key))

    async def held(self, key: SlotKey) -> bool:
        return (
            bool(await self._redis(self.holds.owner(key))) if self.holds is not None else False
        )

    async def acquire(self, key: SlotKey, owner: HoldOwner) -> int:
        require_current_epoch()
        if self.holds is None:
            raise BookingError("HOLD_UNAVAILABLE")
        if not await self.available(key):
            raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE")
        call = await self.database(self.store.call(call_id=owner.call_id))
        if call is None:
            raise BookingError("APPOINTMENT_NOT_FOUND")
        remaining = await self._redis(self.holds.acquire(key, owner))
        try:
            require_current_epoch()
        except asyncio.CancelledError:
            await self.release(key, owner)
            raise
        if remaining <= 0:
            raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE")
        # Redis did not keep a database connection checked out. Availability may have changed.
        try:
            if not await self.available(key):
                raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE")
        except BookingError:
            await self.release(key, owner)
            raise
        return remaining

    async def release(self, key: SlotKey, owner: HoldOwner) -> bool:
        if self.holds is None:
            return False
        try:
            return await self._redis(self.holds.release(key, owner))
        except BookingError:
            # TTL is the cleanup guarantee. Failure after SQL commit cannot undo a booking.
            _log.warning("booking hold cleanup unavailable; lease will expire")
            return False

    async def _existing(self, record: AppointmentRecord):
        require_current_epoch()
        existing = await self.database(self.store.get(record.id))
        if existing is not None:
            fields = (
                "branch_id",
                "appointment_date",
                "start_time",
                "caller_id",
                "call_id",
                "counsellor_id",
                "course_id",
            )
            if existing.status not in ("booked", "confirmed") or any(
                getattr(existing, field) != getattr(record, field) for field in fields
            ):
                raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE")
        return existing

    async def confirm(self, record: AppointmentRecord, *, owner: HoldOwner | None = None):
        require_current_epoch()
        key = SlotKey(record.branch_id, record.appointment_date, record.start_time)
        existing = await self._existing(record)
        if existing is not None:
            return existing
        if not await self.available(key):
            # The first request may have committed after our initial idempotency lookup.
            existing = await self._existing(record)
            if existing is not None:
                return existing
            raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE")
        if owner is not None and owner.call_id != record.call_id:
            raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE")
        if self.holds is not None:
            try:
                current = await self._redis(self.holds.owner(key))
            except BookingError:
                current = None  # Holds are advisory; SQL remains the final authority.
            if current is not None and (owner is None or current != owner.value):
                raise BookingError("APPOINTMENT_SLOT_UNAVAILABLE")
        # Expiry/lost hold is allowed, but only the locked SQL re-check can authorize success.
        require_current_epoch()
        saved = await self.database(self.store.reserve(record))
        if owner is not None:
            await self.release(key, owner)
        return saved

    async def update(self, appointment_id: UUID, **values):
        return await self.database(self.store.update(appointment_id, **values))

    async def hold(self, key: SlotKey, call_id: UUID, token: UUID | None = None):
        owner = HoldOwner(call_id, token or uuid4())
        remaining = await self.acquire(key, owner)
        return {
            "hold_token": str(owner.token),
            "expires_in_ms": remaining,
            "branch_id": str(key.branch_id),
            "appointment_date": key.appointment_date.isoformat(),
            "start_time": key.start_time.isoformat(),
        }
