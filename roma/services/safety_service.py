"""Final deterministic check and bounded, structured audit before speech."""

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from roma.domain.safety.audit import SafetyActivation, SafetyContext, activation, check_sentence
from roma.domain.safety.lexicon import HARD_FAIL_LINE, BlockCategory
from roma.domain.safety.pronunciation import (
    SafetyPronunciation,
    pronunciation_for,
    validate_pronunciation,
)

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
    canonical_text: str | None = None


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

    async def check_pronunciation(
        self, pronunciation: SafetyPronunciation, context: SafetyContext
    ) -> SafeSpeech:
        """Adjudicate only a pinned local rendering; never a caller allowlist.

        Full independent screening remains mandatory. The pinned certificate
        deferral has the checker's known generic-CERT false positive; only that
        exact binding may retain that category. Every other violation/error fails
        before model lookup, cache access or inference.
        """
        validate_pronunciation(pronunciation)
        _, verdict, _ = check_sentence(pronunciation.spoken_text)
        expected_certificate = (
            pronunciation.name == "certificate"
            and verdict.category == BlockCategory.CERT
            and verdict.violations == (BlockCategory.CERT,)
            and verdict.reason == "block:CERT"
        )
        if not verdict.allowed and not expected_certificate:
            raise ValueError("Safety pronunciation failed its independent rule check")
        # Stable binding identity keeps canonical + spoken provenance recoverable
        # without recording raw caller/model input in the safety event.
        identity = sha256("|".join(pronunciation.identity).encode()).hexdigest()
        event = activation(
            f"speech.pronunciation.v1.{identity}",
            "PRONUNCIATION",
            pronunciation.canonical_text,
        )
        ok = await self.persist(context, (event,))
        if not ok:
            # Explicit bounded outage route: no recursion or unaudited original.
            fallback = pronunciation_for(HARD_FAIL_LINE)
            if fallback is None:
                raise ValueError("Approved hard-fail pronunciation is unavailable")
            _, fallback_verdict, _ = check_sentence(fallback.spoken_text)
            if not fallback_verdict.allowed:
                raise ValueError("Hard-fail pronunciation failed independent safety")
            pronunciation = fallback
        return SafeSpeech(pronunciation.spoken_text, (event,), ok, pronunciation.canonical_text)
