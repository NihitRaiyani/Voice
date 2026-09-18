"""Store an outbound lead before asking Twilio to place the call."""

import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from roma.dialer.leadstore import OutboundLead, RedisLeadStore
from roma.llm.prompts import opening_line

_log = logging.getLogger("roma.dialer")

LEAD_TOKEN_BYTES = 32


class DialPrereqError(RuntimeError):
    """A prerequisite of the dial failed BEFORE the carrier was asked to do anything.

    Exists so the API layer can tell "our own store is down" apart from "Twilio refused the
    call". Both used to surface as `502 carrier refused (<type>)`, which sent the operator
    to the carrier dashboard for a Redis outage.
    """


@dataclass(frozen=True)
class TriggeredCall:
    """What the trigger returns. `request_uuid` is Twilio's Call SID."""

    lead_token: str
    answer_url: str
    request_uuid: "str | None"
    to: str
    from_: str


def build_answer_url(base_url: str, lead_token: str) -> str:
    """`<PUBLIC_BASE_URL>/answer?lead=<token>` — the only channel for the lead record.

    `urlencode` rather than an f-string: the token is `secrets.token_urlsafe`, which emits
    `-` and `_` but the encoder is what guarantees that stays true if the alphabet changes.
    """
    return f"{base_url.rstrip('/')}/answer?{urlencode({'lead': lead_token})}"


async def trigger_outbound_call(
    lead: OutboundLead,
    *,
    store: RedisLeadStore,
    client,
    from_number: str,
    base_url: str,
    opener_store=None,
    render=None,
) -> TriggeredCall:
    """Store the lead under a fresh token, then fire the call at its own answer URL.

    Order matters: the record is written BEFORE the call is placed. Twilio fetches the answer
    URL the instant the callee picks up, which on a fast pickup is well under a second — a
    write that happened after the POST would race the carrier and Roma would answer a call
    knowing nothing about the person she dialled.
    """
    lead_token = secrets.token_urlsafe(LEAD_TOKEN_BYTES)
    try:
        await store.put(lead_token, lead)
    except Exception as exc:
        # Fail CLOSED, but say whose fault it is. Dialing anyway would ring a real person
        # whose record Roma cannot fetch at pickup — a call that opens knowing nothing.
        raise DialPrereqError("call store (redis) unavailable") from exc

    answer_url = build_answer_url(base_url, lead_token)
    call = client.calls.create(
        to=lead.phone,
        from_=from_number,
        url=answer_url,
        method="POST",
    )

    # Render the opener into the RING, after the POST rather than before it. The clip is a
    # latency optimisation and the call is the product: a slow or failed render must delay
    # nothing and drop nothing, so it happens once the phone is already ringing and every
    # failure is swallowed. A miss at pickup costs the second it costs today
    # (`PickupGreeter` falls back to synthesis); making the dial wait on Bulbul would cost
    # the call itself.
    if opener_store is not None and render is not None:
        try:
            await opener_store.put(lead_token, await render(opening_line(lead.lead_name)))
            _log.info("opener pre-rendered into the ring")
        except Exception:  # noqa: BLE001 — the fast path is additive, never load-bearing
            # `exception`, not `warning`: swallowing the reason turns a transient Sarvam
            # timeout and a permanent wiring bug into the same one-line message, and the
            # greeting silently reverts to the slow path either way. Call 98aa06a3 lost a
            # render to this and the log could not say why.
            _log.exception("opener pre-render failed; the greeting will be synthesized live")

    request_uuid = str(call.sid)
    # The lead's number is PII (docs/07) and the token is an authority: neither is logged.
    _log.info(
        "outbound call fired: request_uuid=%s segment=%s named=%s",
        request_uuid,
        lead.segment or "-",
        bool(lead.lead_name),
    )
    return TriggeredCall(
        lead_token=lead_token,
        answer_url=answer_url,
        request_uuid=request_uuid,
        to=lead.phone,
        from_=from_number,
    )


def lead_token_from_query(params) -> "str | None":
    """Read `?lead=` off the answer request. Accepts anything with a `.get`."""
    token = params.get("lead") if params is not None else None
    return token or None


__all__ = [
    "DialPrereqError",
    "trigger_outbound_call",
    "build_answer_url",
    "lead_token_from_query",
    "TriggeredCall",
    "LEAD_TOKEN_BYTES",
]
