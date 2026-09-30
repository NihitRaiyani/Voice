"""Map an authenticated Twilio answer request to its stable business identity."""

import hashlib
import json
import re
from collections.abc import Mapping

from roma.domain.webhooks import AnswerEvent


def answer_event(params: Mapping[str, str], lead_token: str | None) -> AnswerEvent:
    call_sid = params.get("CallSid", "")
    account_sid = params.get("AccountSid", "")
    if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call_sid):
        raise ValueError("invalid CallSid")
    if not re.fullmatch(r"AC[0-9a-fA-F]{32}", account_sid):
        raise ValueError("invalid AccountSid")
    raw_direction = params.get("Direction")
    if raw_direction not in (None, "inbound", "outbound-api", "outbound-dial"):
        raise ValueError("invalid Direction")
    if raw_direction is None:
        direction = "outbound" if lead_token else "inbound"
    else:
        direction = "inbound" if raw_direction == "inbound" else "outbound"
    # Only business inputs participate. Changing retry headers or incidental provider
    # metadata must not create a second event. Never persist the bearer-like lead token.
    fingerprint = json.dumps(
        [account_sid, call_sid, direction, lead_token], separators=(",", ":")
    )
    return AnswerEvent(
        provider_event_id=f"twilio:{account_sid}:{call_sid}:answer",
        provider_call_id=call_sid,
        direction=direction,
        payload_digest=hashlib.sha256(fingerprint.encode()).hexdigest(),
    )
