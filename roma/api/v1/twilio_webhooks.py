"""Twilio HTTP webhook adapter."""

import logging
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request, Response

from roma.domain.webhooks import WebhookConflict, WebhookUnavailable
from roma.providers.telephony.twilio.auth import external_url, valid_twilio_signature
from roma.providers.telephony.twilio.twiml import connect_stream_twiml
from roma.providers.telephony.twilio.webhooks import answer_event
from roma.services.call_service import lead_token_from_query

_log = logging.getLogger("roma.api.twilio")


def mount_answer_route(app, *, settings, on_connected) -> None:
    """Mount the signed Twilio answer webhook."""

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        if (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/x-www-form-urlencoded"
        ):
            raise HTTPException(status_code=415, detail="expected form-encoded webhook")
        try:
            pairs = parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid form encoding") from exc
        params = dict(pairs)
        if len(params) != len(pairs):
            raise HTTPException(status_code=400, detail="duplicate form fields")
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

        if params.get("AccountSid") != settings.twilio_account_sid.get_secret_value():
            raise HTTPException(status_code=403, detail="unexpected Twilio account")

        call_id = params.get("CallSid")
        lead_token = lead_token_from_query(request.query_params)
        try:
            event = answer_event(params, lead_token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        service = getattr(app.state, "webhook_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="webhook persistence unavailable")
        try:
            created = await service.accept_answer(event)
        except WebhookConflict as exc:
            raise HTTPException(status_code=409, detail="webhook identity conflict") from exc
        except WebhookUnavailable as exc:
            _log.warning("answer persistence unavailable; delivery may be retried")
            raise HTTPException(
                status_code=503,
                detail="webhook persistence unavailable",
                headers={"Retry-After": "1"},
            ) from exc
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
            headers={"X-Webhook-Duplicate": "false" if created else "true"},
        )


__all__ = ["mount_answer_route"]
