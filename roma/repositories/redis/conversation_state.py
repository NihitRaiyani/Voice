"""Per-call state store with resume-on-drop (docs/06).

Redis holds one checkpoint per call at `call:{call_sid}:state`, written on durable events
only (phase transition, slot fill, lock — NOT every token) and expiring a few hours after
the call. On reconnect the pipeline loads the last checkpoint so Roma resumes at the phase
reached, with discovery answers intact — never re-asking a filled slot (docs/06).

`CallStateStore` is the seam: tests and local dev use `InMemoryCallStateStore` (no live
Redis); prod uses `RedisCallStateStore`. Keying is by `call_sid` for Step 4 — that resumes
a dropped/reconnected websocket. Redial-resume (Twilio issues a NEW call_sid) needs a stable
lead_id and lands with lead-import, which docs/10 does not number (Step 6 is post-call).
"""

import json
from typing import Protocol, runtime_checkable

from roma.domain.conversation.state import CallState

STATE_TTL_SECONDS = 4 * 3600


def state_key(call_sid: str) -> str:
    """The Redis key for a call's checkpoint (docs/06 namespacing)."""
    return f"call:{call_sid}:state"


@runtime_checkable
class CallStateStore(Protocol):
    """Load/save a call's checkpoint. One writer per call:{sid} key by design (docs/06)."""

    async def load(self, call_sid: str) -> "CallState | None": ...

    async def save(self, state: CallState) -> None: ...


class InMemoryCallStateStore:
    """Process-local store for tests and offline runs. Serializes through the same
    to_dict/from_dict path as Redis so a test exercises the real checkpoint shape."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def load(self, call_sid: str) -> "CallState | None":
        raw = self._data.get(state_key(call_sid))
        return CallState.from_dict(json.loads(raw)) if raw is not None else None

    async def save(self, state: CallState) -> None:
        self._data[state_key(state.call_sid)] = json.dumps(state.to_dict())


class RedisCallStateStore:
    """Redis-backed checkpoint (docs/06). Pass a `redis_url` (prod, from config.redis_url)
    or inject an async client (tests use fakeredis). The `redis` import is lazy so the
    controller package imports cleanly without redis installed."""

    def __init__(
        self, redis_url: "str | None" = None, *, client=None, ttl: int = STATE_TTL_SECONDS
    ) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("RedisCallStateStore needs a redis_url or a client")
            from redis import asyncio as redis_asyncio

            client = redis_asyncio.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._client = client
        self._ttl = ttl

    async def load(self, call_sid: str) -> "CallState | None":
        raw = await self._client.get(state_key(call_sid))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return CallState.from_dict(json.loads(raw))

    async def save(self, state: CallState) -> None:
        await self._client.set(
            state_key(state.call_sid), json.dumps(state.to_dict()), ex=self._ttl
        )

    async def aclose(self) -> None:
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()


__all__ = [
    "CallStateStore",
    "InMemoryCallStateStore",
    "RedisCallStateStore",
    "state_key",
    "STATE_TTL_SECONDS",
]
