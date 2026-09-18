"""Store an outbound lead before asking Twilio to place the call."""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from roma.domain.calls.dnd import DenylistRegistry, is_indian_mobile, numbers_from_config
from roma.domain.calls.precall import precall_check
from roma.domain.calls.window import CALL_TIMEZONE
from roma.domain.conversation.prompts import opening_line
from roma.domain.costs.spend import SpendLedger
from roma.providers.telephony.twilio.client import build_twilio_client
from roma.realtime.render import render_trimmed
from roma.repositories.redis.call_status import CallStatusStore, HourlyDialCap
from roma.repositories.redis.leads import OutboundLead, RedisLeadStore
from roma.repositories.redis.opener_audio import OpenerStore
from roma.workers.postcall.paths import spend_ledger_path

_log = logging.getLogger("roma.domain.calls")

LEAD_TOKEN_BYTES = 32


class DialPrereqError(RuntimeError):
    """A prerequisite of the dial failed BEFORE the carrier was asked to do anything.

    Exists so the API layer can tell "our own store is down" apart from "Twilio refused the
    call". Both used to surface as `502 carrier refused (<type>)`, which sent the operator
    to the carrier dashboard for a Redis outage.
    """


@dataclass(frozen=True)
class CallServiceError(RuntimeError):
    """Transport-neutral failure returned by the outbound-call use case."""

    status_code: int
    detail: Any


async def _put_dialing_guarded(status_store, request_uuid, lead_token, to_number) -> None:
    """Persist status after dialing without turning a successful dial into an error."""
    try:
        await status_store.put_dialing(request_uuid, lead_token, to_number)
    except Exception:  # noqa: BLE001 — the phone is already ringing
        _log.info("call status write failed for request_uuid=%s", request_uuid)


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


async def place_outbound_call(to_number: str, *, settings, reachable_fn) -> dict[str, str]:
    """Run every outbound-call gate and coordinate repositories and Twilio.

    This is the application boundary. HTTP handlers supply input and translate
    ``CallServiceError``; they do not own calling-window, DND, budget, reachability,
    rate-limit, persistence, or provider rules.
    """
    if not is_indian_mobile(to_number):
        raise CallServiceError(
            400,
            "to_number must be an Indian mobile in E.164, e.g. +919876543210",
        )

    registry = DenylistRegistry(suppressed=numbers_from_config(settings.dnd_numbers))
    verdict = precall_check(
        to_number,
        datetime.now(CALL_TIMEZONE),
        registry,
        spend=SpendLedger(spend_ledger_path(settings)),
        budget_inr=settings.openai_budget_inr,
    )
    if not verdict.may_dial:
        _log.info("dial refused: %s", verdict.reason)
        raise CallServiceError(409, {"status": "blocked", "reason": verdict.reason})

    reachable, why = await reachable_fn(settings.public_base_url)
    if not reachable:
        _log.info("dial refused: %s", why)
        raise CallServiceError(503, {"status": "blocked", "reason": why})

    cap = HourlyDialCap(
        settings.redis_url.get_secret_value(), limit=settings.max_calls_per_hour
    )
    if not await cap.take():
        reason = f"cap:hourly ({settings.max_calls_per_hour}/hour)"
        _log.info("dial refused: %s", reason)
        raise CallServiceError(429, {"status": "blocked", "reason": reason})

    try:
        client = build_twilio_client()
    except RuntimeError as exc:
        raise CallServiceError(503, f"cannot dial: {exc}") from exc

    status_store = CallStatusStore(
        settings.redis_url.get_secret_value(), ttl=settings.call_status_ttl_secs
    )
    try:
        fired = await trigger_outbound_call(
            OutboundLead(phone=to_number, branch="Vadodara"),
            store=RedisLeadStore(settings.redis_url.get_secret_value()),
            client=client,
            from_number=settings.twilio_from_number.get_secret_value(),
            base_url=settings.public_base_url.rstrip("/"),
            opener_store=OpenerStore(settings.redis_url.get_secret_value()),
            render=lambda text: render_trimmed(
                settings.sarvam_api_key.get_secret_value(), text
            ),
        )
    except DialPrereqError as exc:
        _log.info("dial refused: %s", exc)
        raise CallServiceError(
            503, {"status": "blocked", "reason": str(exc)}
        ) from exc
    except Exception as exc:
        _log.info("dial failed upstream: %s", type(exc).__name__)
        raise CallServiceError(
            502,
            {
                "status": "failed",
                "reason": f"carrier refused ({type(exc).__name__})",
            },
        ) from exc

    _log.info("dial fired: request_uuid=%s", fired.request_uuid)
    await _put_dialing_guarded(
        status_store, fired.request_uuid, fired.lead_token, to_number
    )
    return {"request_uuid": fired.request_uuid, "status": "dialing"}


async def get_call_status(request_uuid: str, *, settings) -> dict[str, Any]:
    """Read the public status projection for one call."""
    store = CallStatusStore(
        settings.redis_url.get_secret_value(), ttl=settings.call_status_ttl_secs
    )
    record = await store.get(request_uuid)
    if record is None:
        raise CallServiceError(404, "unknown call")
    return {"request_uuid": request_uuid, **record}


def lead_token_from_query(params) -> "str | None":
    """Read `?lead=` off the answer request. Accepts anything with a `.get`."""
    token = params.get("lead") if params is not None else None
    return token or None


__all__ = [
    "CallServiceError",
    "DialPrereqError",
    "get_call_status",
    "place_outbound_call",
    "trigger_outbound_call",
    "build_answer_url",
    "lead_token_from_query",
    "TriggeredCall",
    "LEAD_TOKEN_BYTES",
    "_put_dialing_guarded",
]
