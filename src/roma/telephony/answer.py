"""The Vobiz Answer XML — what Roma returns when Vobiz says the callee picked up.

Replaces `twiml.py`. Vobiz POSTs the call's Answer URL and plays whatever XML comes back;
returning a `<Stream>` element opens the WebSocket that carries the conversation.

## Two attributes carry the whole design

**`bidirectional="true"` is mandatory.** This is the same rule the TwiML module recorded and
it survives the move intact: a one-way fork sends the lead's audio to us and plays nothing
back, which is the wrong shape for a voice agent. On Twilio the mistake was `<Start><Stream>`
instead of `<Connect><Stream>`; here it is omitting this attribute. Roma must speak back.

**`keepCallAlive="true"` is mandatory, and omitting it killed every call.** It was left out
deliberately, on the reasoning that Roma's stream *is* the call, so letting the carrier tear
down when the socket closes would remove the need for a REST hang-up. That reasoning was
backwards about what the attribute does: without it Vobiz does not wait for the stream at
all. Measured on the first live call — Vobiz answered at 19:05:00, fetched this XML, **never
opened the WebSocket**, and hung up at 19:05:02. Two seconds, no socket, no error anywhere.
Vobiz's own documented example carries it, and it should have been copied verbatim:

    <Response>
      <Stream bidirectional="true" keepCallAlive="true">wss://your-domain.com/ws</Stream>
    </Response>

The dead-air worry that motivated leaving it out is real but is a different problem with a
different fix: with `keepCallAlive` true the line stays open after Roma stops talking, so
teardown becomes ours to do (the REST hang-up in `telephony/vobiz.py`, now that the account
credentials exist). Twilio's version of that bug cost 185 seconds of billed dead air on
CA3e7f4c58 — worth guarding, but not by breaking every call.

`contentType` configures the INBOUND direction only; the outbound format is declared per
`playAudio` message by the serializer. Both are μ-law 8 kHz, which is what the rest of the
audio path already runs at — the cached `.ulaw` assets, the recorder and the VAD.
"""

from xml.sax.saxutils import escape, quoteattr

# What Vobiz should SEND us. μ-law at 8 kHz keeps the whole downstream audio path unchanged.
INBOUND_CONTENT_TYPE = "audio/x-mulaw;rate=8000"


def answer_xml(wss_url: str, *, content_type: str = INBOUND_CONTENT_TYPE) -> str:
    """Answer XML connecting the answered call to Roma's WebSocket at `wss_url`.

    `wss_url` is the full socket URL (e.g. ``wss://host/ws``); build it from
    `Settings.public_base_url` at the call site, not here — the same split the TwiML module
    used, so a test can pass a fixed URL without touching settings.

    Escaped rather than string-formatted: the URL carries query parameters (`?t=…` for the
    connection token), and an unescaped `&` between two of them is malformed XML that Vobiz
    would reject at answer time — i.e. on a live call, after the lead has already picked up.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f'  <Stream bidirectional="true" keepCallAlive="true" '
        f"contentType={quoteattr(content_type)}>"
        f"{escape(wss_url)}</Stream>\n"
        "</Response>"
    )


__all__ = ["answer_xml", "INBOUND_CONTENT_TYPE"]
