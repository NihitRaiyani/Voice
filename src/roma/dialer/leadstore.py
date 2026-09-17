"""The lead record a triggered outbound call carries, and where it lives between hops.

## Why a store at all

Vobiz's Call API accepts exactly four fields — `from`, `to`, `answer_url`, `answer_method`
(docs/call/make-call, quoted verbatim in `dialer.trigger`). There is no metadata field, no
custom header, nothing that rides along to the answer webhook. So a triggered call cannot
hand Roma the lead's name, city or segment through the API itself.

What we DO control is the `answer_url`, per call. So the trigger mints a token, writes the
lead record under it, and dials with `…/answer?lead=<token>`. Vobiz echoes that URL back at
us when the callee picks up, and the record is read from the token.

## Why Redis and not a dict

`PendingStreams` (the `/answer` -> `/ws` token) is an in-process dict, and that is already
one reason this process cannot be replicated. A second in-process map would double down on
a constraint we are trying to shed, and this one is worse: the stream token lives ~2 seconds
between two hops of the same request, while a lead record has to survive from trigger until
the callee picks up — through ringing, through a retry, through a redeploy mid-campaign.

Reads are NOT destructive. A carrier that retries the answer webhook must get the same lead
back, not a call that has forgotten who it rang; the TTL is what bounds the record instead.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

_log = logging.getLogger("roma.dialer")

# Trigger -> ring -> pickup, plus room for a carrier retry. Not call duration: the record is
# read once at /answer and once at /ws, both within seconds of the callee answering.
LEAD_TTL_SECONDS = 30 * 60

# The segments Weltec dials. Carried as a VARIABLE, never as a branch: one flow, one set of
# override rules (docs/03). It selects which value proof leads in P3 and turns P2's
# study-or-work question into a confirmation — nothing else.
SEGMENTS = ("student", "working_professional", "unemployed")


def lead_key(token: str) -> str:
    return f"roma:lead:{token}"


@dataclass(frozen=True)
class OutboundLead:
    """What we know before dialling. Everything here is a dynamic variable.

    Inbound knew nothing and asked for all of it; outbound knows the lead up front, which is
    the whole reason the discovery phase changes shape. `phone` is the number we dial.
    """

    phone: str
    lead_name: str = ""
    city: str = ""
    branch: str = "Vadodara"
    segment: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.phone:
            raise ValueError("OutboundLead needs a phone number to dial")
        if self.segment and self.segment not in SEGMENTS:
            raise ValueError(f"unknown segment {self.segment!r}; expected one of {SEGMENTS}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OutboundLead":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def as_state_seed(self) -> dict:
        """The dynamic variables as `CallState` kwargs.

        **A field we do not know is OMITTED, never passed as "".** `CallState.lead_name` and
        `.city` are `str | None`, and `next_discovery_slot()` treats any non-None value as
        already captured — so seeding `lead_name=""` for a lead whose name the CRM lacks
        would mark the name permanently filled, Roma would never ask it, and
        `filled_discovery_count()` would be inflated by two, cutting P2 short. That is the
        exact trap `ce4a2e9` documented when it made the field None-by-default; routing a CRM
        record through here reintroduced it from the other side.

        `branch` and `segment` are plain `str` with defaults and are always safe to pass.

        `extra` is deliberately NOT included: an unexpected key would reach prompt assembly,
        and `llm.prompts._render` raises on anything it cannot resolve. Extra fields are for
        the CRM round-trip, not for the model.
        """
        seed: dict[str, Any] = {
            "branch": self.branch or "Vadodara",
            "segment": self.segment or "",
        }
        if self.lead_name:
            seed["lead_name"] = self.lead_name
        if self.city:
            seed["city"] = self.city
        return seed


class RedisLeadStore:
    """Token -> lead record, TTL-bounded. Mirrors `controller.store.RedisCallStateStore`.

    Pass a `redis_url` (prod) or inject a client (tests use fakeredis). The `redis` import is
    lazy so this package imports cleanly without redis installed.
    """

    def __init__(
        self, redis_url: "str | None" = None, *, client=None, ttl: int = LEAD_TTL_SECONDS
    ) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("RedisLeadStore needs a redis_url or a client")
            from redis import asyncio as redis_asyncio

            client = redis_asyncio.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._client = client
        self._ttl = ttl

    async def put(self, token: str, lead: OutboundLead) -> None:
        await self._client.set(lead_key(token), json.dumps(lead.to_dict()), ex=self._ttl)

    async def get(self, token: "str | None") -> "OutboundLead | None":
        """Non-destructive by design — see the module docstring on carrier retries."""
        if not token:
            return None
        raw = await self._client.get(lead_key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return OutboundLead.from_dict(json.loads(raw))
        except (ValueError, TypeError):
            # A malformed record must not take down a call that is already ringing. The call
            # proceeds with an unknown lead, which is survivable; a 500 at /answer is not.
            _log.warning("lead record for token could not be decoded; continuing without it")
            return None

    async def aclose(self) -> None:
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()


__all__ = [
    "OutboundLead",
    "RedisLeadStore",
    "lead_key",
    "LEAD_TTL_SECONDS",
    "SEGMENTS",
]
