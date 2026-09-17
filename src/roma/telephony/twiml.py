from twilio.twiml.voice_response import VoiceResponse


def connect_stream_twiml(wss_url: str, *, lead_token: str | None = None) -> str:
    response = VoiceResponse()
    stream = response.connect().stream(url=wss_url)
    if lead_token:
        stream.parameter(name="lead", value=lead_token)
    return str(response)


__all__ = ["connect_stream_twiml"]
