"""Twilio HTTP webhook adapter."""

import logging
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request, Response

from roma.providers.telephony.twilio.auth import external_url, valid_twilio_signature
from roma.providers.telephony.twilio.twiml import connect_stream_twiml
from roma.services.call_service import lead_token_from_query

_log = logging.getLogger("roma.api.twilio")


def mount_answer_route(app, *, settings, on_connected) -> None:
    """Mount the signed Twilio answer webhook."""

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        params = dict(
            parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True)
        )
        signature_url = external_url(
            settings.public_base_url, request.url.path, request.url.query
        )
        if not valid_twilio_signature(
            signature_url,
            params,
            request.headers.get("x-twilio-signature", ""),
            settings.twilio_auth_token.get_secret_value(),
        ):
            raise HTTPException(status_code=403, detail="invalid Twilio signature")

        call_id = params.get("CallSid")
        lead_token = lead_token_from_query(request.query_params)
        wss_url = external_url(settings.public_base_url, "/ws", websocket=True)
        _log.info(
            "answer: streaming call_id=%s lead=%s",
            call_id or "-",
            "yes" if lead_token else "-",
        )
        await on_connected(app, lead_token)
        return Response(
            content=connect_stream_twiml(wss_url, lead_token=lead_token),
            media_type="application/xml",
        )


__all__ = ["mount_answer_route"]
