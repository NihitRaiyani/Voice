"""Call-scoped calendar and commit gateway. SQL truth wins over Redis checkpoints."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from roma.core.turn_epoch import require_current_epoch
from roma.domain.appointments.booking import BookingError, HoldOwner, SlotKey
from roma.domain.appointments.timeresolve import IST
from roma.domain.persistence import AppointmentRecord


class BookingSession:
    def __init__(self, service, state) -> None:
        self.service, self.state = service, state
        self._call = None
        self._branch = None
        self._leases: dict[SlotKey, HoldOwner] = {}
        self._token = uuid4()

    async def _bind(self):
        require_current_epoch()
        if self.service.store is None:
            raise BookingError("BOOKING_UNAVAILABLE")
        if self._call is None:
            self._call = await self.service.database(
                self.service.store.call(sid=self.state.call_sid)
            )
        if self._call is None:
            raise BookingError("BOOKING_UNAVAILABLE")
        if self._branch is None:
            self._branch = await self.service.database(
                self.service.store.branch_id(self.state.branch)
            )
        if self._branch is None:
            raise BookingError("BOOKING_UNAVAILABLE")
        require_current_epoch()
        return self._call, self._branch

    async def restore(self) -> None:
        require_current_epoch()
        state = self.state
        state.booking_required = True
        state.appointment_id, state.locked_slot = None, None
        state.readback_confirmed = False
        state.slots_offered = []  # A serialized offer is not a live lease.
        if state.slot_status == "locked":
            state.slot_status = "none"
            state.accepted_slot = None
        try:
            call, _ = await self._bind()
            active = await self.service.database(self.service.store.active_for_call(call.id))
            if active is not None:
                iso = datetime.combine(
                    active.appointment_date, active.start_time, IST
                ).isoformat()
                state.appointment_id, state.locked_slot, state.accepted_slot = (
                    str(active.id),
                    iso,
                    iso,
                )
                state.slot_status, state.readback_confirmed, state.booking_error = (
                    "locked",
                    True,
                    None,
                )
                from roma.domain.conversation.stage import ConversationStage

                state.stage = ConversationStage.CLOSE
            else:
                state.booking_error = None
                if state.accepted_slot:
                    if not await self.is_open(datetime.fromisoformat(state.accepted_slot)):
                        state.accepted_slot = None
                        state.slot_status = "unavailable"
                        state.booking_error = "APPOINTMENT_SLOT_UNAVAILABLE"
                    else:
                        state.slot_status = "accepted"
        except BookingError as exc:
            state.booking_error = exc.code
            state.accepted_slot = None
            state.slot_status = "unavailable"

    async def offers(self, now, *, count=2, on=None):
        call, branch = await self._bind()
        candidates = await self.service.candidates(branch, on=on)
        result, keep = [], set()
        for record in candidates:
            key = SlotKey(branch, record.appointment_date, record.start_time)
            owner = self._leases.get(key, HoldOwner(call.id, self._token))
            try:
                await self.service.acquire(key, owner)
            except BookingError as exc:
                if exc.code == "APPOINTMENT_SLOT_UNAVAILABLE":
                    continue
                if exc.code != "HOLD_UNAVAILABLE":
                    raise
                # Redis is advisory: SQL can offer a time without claiming a lease.
                if not await self.service.available(key):
                    continue
            else:
                self._leases[key] = owner
            keep.add(key)
            result.append(datetime.combine(key.appointment_date, key.start_time, IST))
            if len(result) >= count:
                break
        await self._release_except(keep)
        require_current_epoch()
        return result

    async def is_open(self, when):
        call, branch = await self._bind()
        local = when.astimezone(IST)
        key = SlotKey(branch, local.date(), local.time())
        owner = self._leases.get(key, HoldOwner(call.id, self._token))
        try:
            await self.service.acquire(key, owner)
        except BookingError as exc:
            if exc.code == "APPOINTMENT_SLOT_UNAVAILABLE":
                return False
            if exc.code != "HOLD_UNAVAILABLE":
                raise
            if not await self.service.available(key):
                return False
        else:
            self._leases[key] = owner
        await self._release_except({key})
        require_current_epoch()
        return True

    async def confirm(self, iso: str) -> str:
        call, branch = await self._bind()
        local = datetime.fromisoformat(iso).astimezone(IST)
        key = SlotKey(branch, local.date(), local.time())
        now = self.service.now().astimezone(UTC)
        record = AppointmentRecord(
            id=uuid5(NAMESPACE_URL, f"roma:call-booking:{call.id}"),
            branch_id=branch,
            appointment_date=local.date(),
            start_time=local.time(),
            caller_id=call.caller_id,
            call_id=call.id,
            status="confirmed",
            created_at=now,
            updated_at=now,
        )
        # A timed-out acquire may have committed its lease before losing the reply.
        owner = self._leases.get(key, HoldOwner(call.id, self._token))
        saved = await self.service.confirm(record, owner=owner)
        await self.aclose()
        require_current_epoch()
        return str(saved.id)

    async def _release_except(self, keep):
        for key, owner in tuple(self._leases.items()):
            if key not in keep:
                await self.service.release(key, owner)
                self._leases.pop(key, None)

    async def aclose(self):
        await self._release_except(set())
