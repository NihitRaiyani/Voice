"""Bounded best-effort Pub/Sub with SQL snapshots for reconnects.

Delivery is ephemeral and never substitutes for durable events or checkpoints.
Only metadata crosses Redis/WebSocket; caller content never enters this module.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from roma.schemas.supervisor import SupervisorEvent

CHANNEL = "roma:supervisor:v1"


async def relay_supervisor(websocket, redis, authorize, *, heartbeat=5.0, capacity=16):
    """Slow clients disconnect and resync; revoked/expired principals lose access."""
    async with asyncio.timeout(2):
        if not await authorize():
            raise PermissionError("Supervisor permission required")
    queue = asyncio.Queue(maxsize=capacity)
    pubsub = redis.pubsub()

    async def read():
        while True:
            async with asyncio.timeout(2):
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
            if message is None:
                await asyncio.sleep(0.01)
                continue
            raw = message.get("data")
            if not isinstance(raw, (str, bytes)) or len(raw) > 4096:
                continue
            try:
                value = SupervisorEvent.model_validate_json(raw)
            except (ValidationError, ValueError):
                continue
            try:
                queue.put_nowait(value)
            except asyncio.QueueFull:
                raise BufferError("Supervisor resync required") from None

    async def write():
        while True:
            value = await queue.get()
            async with asyncio.timeout(1):
                await websocket.send_json(value.model_dump(mode="json"))

    async def check():
        while True:
            await asyncio.sleep(heartbeat)
            async with asyncio.timeout(2):
                if not await authorize():
                    raise PermissionError("Supervisor permission expired")

    tasks = []
    try:
        async with asyncio.timeout(2):
            await pubsub.subscribe(CHANNEL)
        # Subscribe before requesting the SQL snapshot. Clients deduplicate event_id.
        async with asyncio.timeout(1):
            await websocket.send_json(
                {"kind": "resync_required", "snapshot": "/api/v1/supervisor/snapshot"}
            )
        tasks = [asyncio.create_task(fn()) for fn in (read, write, check)]
        if hasattr(websocket, "receive_text"):

            async def disconnect():
                await websocket.receive_text()
                raise PermissionError("No additional client messages accepted")

            tasks.append(asyncio.create_task(disconnect()))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            async with asyncio.timeout(1):
                await pubsub.aclose()


@dataclass(frozen=True)
class StateObservation:
    sid: str
    at: datetime
    stage: str
    turn: int
    language: str
    greeting: bool
    discovery: bool
    value: bool
    offered: bool
    accepted: bool


@dataclass(frozen=True)
class TurnObservation:
    sid: str
    turn: int
    stage: str
    latency_ms: int


class SupervisorHub:
    def __init__(self, factory, redis, *, capacity=64):
        self.factory, self.redis = factory, redis
        self.queue = asyncio.Queue(maxsize=capacity)
        self.dropped = 0
        self.task = None

    def on_state(self, state, *, milestone=None):
        """Called only after durable save; queue admission never awaits call work."""
        value = StateObservation(
            state.call_sid,
            datetime.now(UTC),
            state.stage.value,
            state.turn_count,
            (
                {
                    "GUJARATI": "gu",
                    "HINDI": "hi",
                    "ENGLISH": "en",
                    "GUJARATI_ENGLISH": "code_mix",
                    "HINDI_ENGLISH": "code_mix",
                }.get(state.language_router.current.value, "unknown")
                if state.language_router.recent
                else "unknown"
            ),
            state.inquiry_confirmed,
            state.filled_discovery_count() == 5,
            bool(state.facts_said),
            milestone == "turn_completed"
            and state.stage.value in {"pivot", "close"}
            and bool(state.slots_offered),
            bool(state.accepted_slot) and state.slot_status in {"accepted", "locked"},
        )
        try:
            self.queue.put_nowait(value)
        except asyncio.QueueFull:
            self.dropped += 1

    def on_turn(self, sid, turn, stage, latency_ms):
        try:
            self.queue.put_nowait(TurnObservation(sid, turn, stage, round(latency_ms)))
        except asyncio.QueueFull:
            self.dropped += 1

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def run(self):
        from roma.repositories.postgres.supervisor import SupervisorRepository

        while True:
            observed = await self.queue.get()
            try:
                # SQL work commits before external Pub/Sub; no connection during publish.
                async with asyncio.timeout(2):
                    async with self.factory() as session:
                        repository = SupervisorRepository(session)
                        call_id = (
                            await repository.observe_turn(observed)
                            if isinstance(observed, TurnObservation)
                            else await repository.observe(observed)
                        )
                        await session.commit()
                        events = await repository.snapshot(call_id=call_id) if call_id else []
                    for event in events:
                        await self.redis.publish(CHANNEL, event.model_dump_json())
            except Exception:  # noqa: BLE001 — transient delivery failure cannot fail a call.
                self.dropped += 1
            finally:
                self.queue.task_done()

    async def aclose(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
