"""Roma's Twilio voice and bidirectional Media Streams spine."""

from roma.providers.telephony.twilio.client import DialResult, place_call
from roma.providers.telephony.twilio.twiml import connect_stream_twiml

__all__ = ["place_call", "DialResult", "connect_stream_twiml", "build_media_app"]


def __getattr__(name: str):
    if name == "build_media_app":
        from roma.realtime.pipeline import build_media_app

        return build_media_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
