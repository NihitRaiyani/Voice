"""Roma telephony spine (docs/10 Step 1).

Outbound Vobiz call + a bidirectional `<Stream>` websocket into a bare Pipecat transport,
gated by the Gate-0 pre-dial check and speaking only filter-approved canned audio.

Vobiz carries the SIP leg to the PSTN itself and forks the call audio to us over a WebSocket,
so nothing here speaks SIP — see `docs/decisions.md` for why that decided the whole shape.

`build_media_app` pulls in the Pipecat/FastAPI stack, so it is imported lazily —
`roma.telephony.dialer` / `answer` / `canned` stay usable without spinning that up.
"""

from roma.telephony.answer import answer_xml
from roma.telephony.dialer import DialResult, place_call

__all__ = ["place_call", "DialResult", "answer_xml", "build_media_app"]


def __getattr__(name: str):
    if name == "build_media_app":
        from roma.telephony.media import build_media_app

        return build_media_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
