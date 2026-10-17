"""Booking identity and failures; Redis leases never imply an appointment exists."""

from dataclasses import dataclass
from datetime import date, time
from uuid import UUID


class BookingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SlotKey:
    branch_id: UUID
    appointment_date: date
    start_time: time

    @property
    def redis_key(self) -> str:
        return f"hold:{self.branch_id}:{self.appointment_date.isoformat()}:{self.start_time.isoformat(timespec='microseconds')}"


@dataclass(frozen=True)
class HoldOwner:
    call_id: UUID
    token: UUID

    @property
    def value(self) -> str:
        return f"{self.call_id}:{self.token}"


BOOKING_UNAVAILABLE_LINE = "Abhi visit ka time confirm nahi hua hai. Counsellor se sahi availability check karwa dete hain."
SLOT_CONFLICT_LINE = "Woh visit time ab uplabdh nahi hai. Doosra available time dekh lete hain."


def confirmed_line(slot: str, branch: str) -> str:
    return (
        f"Aapki {branch} branch visit {slot} ke liye confirm ho gayi hai. Shukriya, milte hain."
    )
