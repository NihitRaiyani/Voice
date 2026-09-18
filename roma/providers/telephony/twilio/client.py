"""Safety-gated outbound dialing through Twilio Programmable Voice."""

import logging
from dataclasses import dataclass
from datetime import datetime

from roma.domain.calls import DoNotCallRegistry, precall_check

_log = logging.getLogger("roma.realtime")
DIAL_TIMEOUT_SECS = 15.0


@dataclass(frozen=True)
class DialResult:
    dialed: bool
    call_sid: str | None
    reason: str
    consent_line: str | None


def _created_call_sid(call) -> str | None:
    value = getattr(call, "sid", None)
    return str(value) if value else None


def place_call(
    phone: str,
    now: datetime,
    registry: DoNotCallRegistry,
    client,
    from_number: str,
    answer_url: str,
    *,
    spend=None,
    budget_inr: float | None = None,
) -> DialResult:
    """Run Gate 0, then place exactly one Twilio call when permitted."""
    verdict = precall_check(phone, now, registry, spend=spend, budget_inr=budget_inr)
    if not verdict.may_dial:
        _log.info("pre-dial gate blocked call: %s", verdict.reason)
        return DialResult(False, None, verdict.reason, None)

    call = client.calls.create(
        to=phone,
        from_=from_number,
        url=answer_url,
        method="POST",
    )
    call_sid = _created_call_sid(call)
    _log.info("outbound call placed: call_sid=%s", call_sid)
    return DialResult(True, call_sid, "ok", verdict.consent_line)


def build_twilio_client():
    """Construct the official Twilio client at the outbound API boundary."""
    from twilio.http.http_client import TwilioHttpClient
    from twilio.rest import Client

    from roma.core.config import get_settings

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


__all__ = ["place_call", "DialResult", "build_twilio_client"]
