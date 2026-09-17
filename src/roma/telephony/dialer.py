"""Outbound dialer (docs/10 Step 1) — the Vobiz wiring on top of the Gate-0 gate.

The one rule (docs/10 Gate 0, mirrored from src/roma/dialer/precall.py): call
`precall_check` and place the call ONLY on `may_dial=True`, and the consent line the
gate hands back is spoken first (the media handler does the speaking; see media.py).

The REST client is injected, never constructed inline: prod passes a real client, tests pass
a mock. `build_vobiz_client()` is the prod wiring that reads secrets at the API boundary
(docs/07: `.get_secret_value()` only here).

## What replaced what

Twilio took its TwiML *inline* on `calls.create(twiml=…)`, so it never called back into this
host over HTTP. Vobiz takes an `answer_url` and fetches the XML when the callee picks up
(`telephony/answer.py` serves it). That is why `place_call` now takes an answer URL rather
than a socket URL, and why the process must be publicly reachable on HTTPS as well as WSS.

The client is a thin object rather than a vendor SDK because the whole surface Roma needs is
one POST. Vobiz's API is Plivo-shaped — `/api/v1/Account/{auth_id}/Call/` — but authenticates
with `X-Auth-ID`/`X-Auth-Token` headers rather than HTTP basic.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from roma.dialer import DoNotCallRegistry, precall_check

_log = logging.getLogger("roma.telephony")

VOBIZ_API_BASE = "https://api.vobiz.ai/api/v1"

# A dial is a single short POST on a path a human is waiting on. Bounded for the same reason
# every other outbound call in this codebase is: the SDK-default of "wait forever" turns a
# carrier hiccup into a hung dialer with nothing to show for it.
DIAL_TIMEOUT_SECS = 15.0


@dataclass(frozen=True)
class DialResult:
    dialed: bool
    call_sid: "str | None"
    reason: str
    consent_line: "str | None"


class VobizClient:
    """The one Vobiz REST call Roma makes. Injected so tests never touch the network."""

    def __init__(
        self, auth_id: str, auth_token: str, *, base_url: str = VOBIZ_API_BASE
    ) -> None:
        self._auth_id = auth_id
        self._auth_token = auth_token
        self._base_url = base_url.rstrip("/")

    def create_call(self, *, to: str, from_: str, answer_url: str) -> "dict":
        """POST a call request. Returns the decoded JSON body.

        Synchronous on purpose: this runs from `scripts/place_test_call.py`, never from the
        media event loop, and the dialer has always been a sync function.
        """
        import httpx

        response = httpx.post(
            f"{self._base_url}/Account/{self._auth_id}/Call/",
            headers={"X-Auth-ID": self._auth_id, "X-Auth-Token": self._auth_token},
            json={"from": from_, "to": to, "answer_url": answer_url, "answer_method": "POST"},
            timeout=DIAL_TIMEOUT_SECS,
        )
        response.raise_for_status()
        return response.json() if response.content else {}


def _call_id_from(payload: "dict | None") -> "str | None":
    """Pull the call identifier out of a create-call response.

    Several spellings are accepted because the exact key is not pinned down in the docs we
    have, and the identifier is only used for logging and as a filename/Redis key — a miss
    costs a less readable log line, never the call. `media.py` learns the authoritative id
    from the stream's `start` event regardless.
    """
    if not isinstance(payload, dict):
        return None
    for key in (
        "call_uuid",
        "callUuid",
        "callUUID",
        "request_uuid",
        "call_id",
        "callId",
        "uuid",
    ):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def place_call(
    phone: str,
    now: datetime,
    registry: DoNotCallRegistry,
    client,
    from_number: str,
    answer_url: str,
    *,
    spend=None,
    budget_inr: "float | None" = None,
) -> DialResult:
    """Gate, then dial. Returns without dialing if the pre-dial gate blocks.

    `client` is a Vobiz REST client (real or mock) exposing `.create_call(...)`.
    `answer_url` is the full HTTPS URL Vobiz fetches on answer (e.g. https://host/answer).

    `spend` (a `roma.spend.SpendLedger`) and `budget_inr` are the OpenAI testing cap. Both
    are injected rather than read from settings here, for the same reason the REST client
    is: this function must stay callable from a test without touching the real ledger.
    Omitting them skips the money gate — `scripts/place_test_call.py` is what supplies them
    on the path that actually spends money.
    """
    verdict = precall_check(phone, now, registry, spend=spend, budget_inr=budget_inr)
    if not verdict.may_dial:
        _log.info("pre-dial gate blocked call: %s", verdict.reason)
        return DialResult(False, None, verdict.reason, None)

    payload = client.create_call(to=phone, from_=from_number, answer_url=answer_url)
    call_id = _call_id_from(payload)
    _log.info("outbound call placed: call_id=%s", call_id)
    return DialResult(True, call_id, "ok", verdict.consent_line)


def build_vobiz_client() -> VobizClient:
    """Prod-only: construct a real Vobiz client from settings. Secrets are read
    via `.get_secret_value()` here — the exact API boundary (docs/07).

    This is where missing account credentials are caught. They are optional in `Settings`
    because the media server does not need them — Vobiz authenticates its own trunk and
    connects to us, so answering and streaming work without them and so does the whole
    offline gate. Placing a call does not, and failing here with a sentence naming the two
    variables beats a 401 from a REST call that mentions neither.
    """
    from roma.config import get_settings

    s = get_settings()
    auth_id = s.vobiz_auth_id.get_secret_value()
    auth_token = s.vobiz_auth_token.get_secret_value()
    if not auth_id or not auth_token:
        raise RuntimeError(
            "VOBIZ_AUTH_ID and VOBIZ_AUTH_TOKEN are required to place a call. Copy both "
            "from the Vobiz console dashboard into .env. Everything except outbound dialing "
            "works without them."
        )
    return VobizClient(auth_id, auth_token)


def build_twilio_client():
    """Construct the official Twilio client at the outbound API boundary."""
    from twilio.http.http_client import TwilioHttpClient
    from twilio.rest import Client

    from roma.config import get_settings

    settings = get_settings()
    credentials = (
        ("TWILIO_ACCOUNT_SID", settings.twilio_account_sid.get_secret_value()),
        ("TWILIO_AUTH_TOKEN", settings.twilio_auth_token.get_secret_value()),
        ("TWILIO_FROM_NUMBER", settings.twilio_from_number.get_secret_value()),
    )
    missing = [name for name, value in credentials if not value]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} are required to place a Twilio call. "
            "Add the missing values to .env; server startup and offline verification work "
            "without them."
        )
    http_client = TwilioHttpClient(timeout=DIAL_TIMEOUT_SECS)
    return Client(credentials[0][1], credentials[1][1], http_client=http_client)


__all__ = [
    "place_call",
    "DialResult",
    "VobizClient",
    "build_vobiz_client",
    "build_twilio_client",
    "VOBIZ_API_BASE",
]
