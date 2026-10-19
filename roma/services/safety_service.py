"""Final deterministic check and bounded, structured audit before speech."""

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from typing import Protocol

from roma.domain.safety.audit import SafetyActivation, SafetyContext, check_sentence
from roma.domain.safety.lexicon import HARD_FAIL_LINE

_log = logging.getLogger("roma.safety.audit")


class SafetyEventStore(Protocol):
    async def record(
        self, context: SafetyContext, events: tuple[SafetyActivation, ...]
    ) -> None: ...


class InMemorySafetyEventStore:
    """Synthetic console/test sink only; never claim durable audit from this sink."""

    def __init__(self) -> None:
        self.entries: list[tuple[SafetyContext, SafetyActivation]] = []

    async def record(
        self, context: SafetyContext, events: tuple[SafetyActivation, ...]
    ) -> None:
        if len(self.entries) + len(events) > 4096:
            raise ValueError("Synthetic audit capacity reached")
        self.entries.extend((context, event) for event in events)


class UnavailableSafetyEventStore:
    async def record(
        self, context: SafetyContext, events: tuple[SafetyActivation, ...]
    ) -> None:
        raise RuntimeError("Durable safety audit unavailable")


@dataclass(frozen=True)
class SafeSpeech:
    text: str
    events: tuple[SafetyActivation, ...]
    audit_ok: bool


class SafetyService:
    def __init__(self, store: SafetyEventStore | None = None, *, timeout: float = 0.5) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Audit timeout must be finite and positive")
        self.store = store if store is not None else InMemorySafetyEventStore()
        self.timeout = timeout

    async def persist(
        self, context: SafetyContext, events: tuple[SafetyActivation, ...]
    ) -> bool:
        if not events:
            return True
        try:
            async with asyncio.timeout(self.timeout):
                await self.store.record(context, events)
        except Exception as exc:  # noqa: BLE001 — audit failure must never release unsafe speech
            # This is an explicit failure signal, not a claim that PostgreSQL committed.
            _log.error(
                "safety_audit_write_failed %s",
                json.dumps(
                    {
                        "events": [event.data() for event in events],
                        "error_type": type(exc).__name__,
                    }
                ),
            )
            return False
        return True

    async def check(
        self, text: str, context: SafetyContext, *, extra: tuple[SafetyActivation, ...] = ()
    ) -> SafeSpeech:
        safe, _, events = check_sentence(text)
        all_events = extra + events
        ok = await self.persist(context, all_events)
        return SafeSpeech(safe if ok else HARD_FAIL_LINE, all_events, ok)
