"""Twilio HTTP webhook adapter."""

import asyncio
import logging
import re
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request, Response
from pydantic import ValidationError

from roma.core.call_capabilities import CallCapabilities
from roma.core.logging import LogEvent, log_event
from roma.domain.webhooks import WebhookConflict, WebhookUnavailable
from roma.providers.telephony.twilio.auth import external_url, valid_twilio_signature
from roma.providers.telephony.twilio.twiml import connect_stream_twiml
from roma.providers.telephony.twilio.webhooks import answer_event
from roma.schemas.twilio import AnswerPayload

_log = logging.getLogger("roma.api.twilio")
_MAX_BODY = 16_384


def _reject(status: int, detail: str):
    log_event(LogEvent.SECURITY_REJECTED, status_code=status)
    raise HTTPException(status_code=status, detail=detail)


async def _read_form(request: Request) -> dict[str, str]:
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(body) + len(chunk) > _MAX_BODY:
                    _reject(413, "webhook body too large")
                body.extend(chunk)
    except TimeoutError:
        _reject(408, "webhook body timeout")
    try:
        text = body.decode("utf-8")
        if re.search(r"%(?![0-9a-fA-F]{2})", text):
            raise ValueError
        pairs = parse_qsl(
            text,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=64,
            strict_parsing=True,
        )
    except (UnicodeError, ValueError):
        _reject(400, "invalid form encoding")
    params = dict(pairs)
    if len(params) != len(pairs):
        _reject(400, "duplicate form fields")
    if any(not 1 <= len(key) <= 64 or len(value) > 2048 for key, value in pairs):
        _reject(400, "invalid form field bounds")
    return params


def mount_answer_route(app, *, settings, on_connected) -> None:
    """Mount the signed Twilio answer webhook."""

    @app.post("/answer")
    async def answer(request: Request) -> Response:
        if (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/x-www-form-urlencoded"
        ):
            _reject(415, "expected form-encoded webhook")
        params = await _read_form(request)
        signature_url = external_url(
            settings.public_base_url, request.url.path, request.url.query
        )
        if not valid_twilio_signature(
            signature_url,
            params,
            request.headers.get("x-twilio-signature", ""),
            settings.twilio_auth_token.get_secret_value(),
        ):
            _reject(403, "invalid Twilio signature")

        if params.get("AccountSid") != settings.twilio_account_sid.get_secret_value():
            _reject(403, "unexpected Twilio account")

        try:
            payload = AnswerPayload.model_validate(params)
        except ValidationError:
            _reject(400, "invalid Twilio payload")
        query = request.query_params
        if len(query.multi_items()) != len(query) or len(request.url.query) > 2048:
            _reject(400, "invalid callback query")
        lead_token = query.get("lead") or None
        intent = query.get("intent")
        try:
            capabilities = CallCapabilities(
                settings.twilio_auth_token.get_secret_value(), payload.account_sid
            )
            if intent:
                capabilities.verify_answer(intent, lead_token)
            elif lead_token or payload.direction in ("outbound-api", "outbound-dial"):
                raise ValueError
        except ValueError:
            _reject(403, "invalid callback capability")
        call_id = payload.call_sid
        event = answer_event(params, lead_token)
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
        stream_capability = capabilities.issue_stream(call_id, lead_token)
        log_event(LogEvent.SECURITY_ACCEPTED, status_code=200)
        await on_connected(app, lead_token)
        return Response(
            content=connect_stream_twiml(
                wss_url, lead_token=lead_token, capability=stream_capability
            ),
            media_type="application/xml",
            headers={"X-Webhook-Duplicate": "false" if created else "true"},
        )


__all__ = ["mount_answer_route"]
