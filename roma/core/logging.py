"""Logging setup with secret redaction (docs/07: no secret in logs).

RedactionFilter is defense-in-depth: even if a secret value is accidentally
logged, it is scrubbed from the record before emission.
"""

import asyncio
import json
import logging
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter
from uuid import uuid4

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from roma.core.config import Settings
from roma.core.voice_errors import VoiceErrorCode

_REDACTED = "***REDACTED***"

_MIN_SECRET_LEN = 6

# `?lead=<token>` is minted per call by dialer/trigger.py and cannot be enumerated in
# _secret_values(): it does not exist until the call is placed. Matched on the URL
# grammar instead of on any known value. Deliberately NOT `\w`/`\b` — this is a
# hand-written character class for the same reason the guardrail matchers avoid them.
_LEAD_TOKEN_RE = re.compile(r"([?&]lead=)[A-Za-z0-9_\-%.~]+")

# uvicorn's own loggers are configured by its CLI with `propagate: False` and their own
# handlers, so a filter installed on the root handler never sees a single one of their
# records. The documented entrypoint IS the CLI (scripts/serve_media.py), which is how
# the lead token reached disk on every call while `roma.*` logging looked spotless.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _scrub_record(record: logging.LogRecord, scrub) -> None:
    """Rewrite `msg` and `args` in place, PRESERVING THE ARITY OF `args`.

    The obvious implementation — collapse to `record.getMessage()`, set `args = ()` —
    breaks uvicorn. `uvicorn.logging.AccessFormatter.formatMessage` unpacks `record.args`
    into exactly five values (client_addr, method, full_path, http_version, status_code),
    so an emptied tuple raises ValueError inside the logging machinery on every request:
    the redaction becomes a crash. Scrub the strings where they sit instead.
    """
    if isinstance(record.msg, str):
        record.msg = scrub(record.msg)
    if isinstance(record.args, tuple):
        record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
    elif isinstance(record.args, dict):
        record.args = {k: scrub(v) if isinstance(v, str) else v for k, v in record.args.items()}


class RedactionFilter(logging.Filter):
    """Scrub known secret values out of every log record before emission."""

    def __init__(self, secrets):
        super().__init__()
        self._secrets = sorted(
            (s for s in secrets if s and len(s) >= _MIN_SECRET_LEN), key=len, reverse=True
        )
        self.skipped_short = sum(1 for s in secrets if s and len(s) < _MIN_SECRET_LEN)

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, _REDACTED)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        _scrub_record(record, self._scrub)
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = self._scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)
        return True


class LeadTokenFilter(logging.Filter):
    """Scrub `?lead=<token>` out of every log record before emission (docs/07).

    The lead token is a bearer credential: it dereferences in Redis to a real lead's
    name, city and segment, and it authorises the `/ws` socket that carries their live
    voice. `roma.realtime` never logs it — but `uvicorn.access` logs the whole request
    line, query string included, and nothing stood between that and the disk.

    This sits alongside RedactionFilter rather than inside it because RedactionFilter
    scrubs a fixed list of configured values; this one scrubs a shape.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        _scrub_record(record, lambda t: _LEAD_TOKEN_RE.sub(rf"\g<1>{_REDACTED}", t))
        return True


current_call_sid: ContextVar[str | None] = ContextVar("current_call_sid", default=None)


class CallSidFilter(logging.Filter):
    """Stamp every record with the call it belongs to, or `-` outside a call."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.call_sid = current_call_sid.get() or "-"
        return True


class _LoggingSettings(BaseSettings):
    """Logging can start without configuring or requiring any provider client."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
        env_ignore_empty=True, hide_input_in_errors=True,
    )
    log_level: str = "INFO"
    twilio_account_sid: SecretStr = SecretStr("")
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_from_number: SecretStr = SecretStr("")
    sarvam_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    redis_url: SecretStr = SecretStr("")


def _secret_values(settings: Settings | _LoggingSettings):
    """Every value that must never appear in a log line (CLAUDE.md gate 2, docs/07)."""
    return [
        settings.twilio_account_sid.get_secret_value(),
        settings.twilio_auth_token.get_secret_value(),
        settings.twilio_from_number.get_secret_value(),
        settings.sarvam_api_key.get_secret_value(),
        settings.openai_api_key.get_secret_value(),
        settings.anthropic_api_key.get_secret_value()
        if hasattr(settings, "anthropic_api_key")
        else "",
        settings.redis_url.get_secret_value(),
    ]


def _attach_to_uvicorn(*filters: logging.Filter) -> None:
    """Install our filters on uvicorn's own loggers.

    Attached to the LOGGER, not to its handlers: a logger-level filter runs in
    `Logger.handle()` before any handler is called, so it holds no matter what handler
    set uvicorn's config happens to install. Idempotent by type — `configure_logging()`
    is called per app build and by the tests, and filters would otherwise stack up.
    """
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        for filt in filters:
            if not any(isinstance(existing, type(filt)) for existing in logger.filters):
                logger.addFilter(filt)


def configure_logging(settings: Settings | None = None) -> None:
    """Install a root handler that redacts known secrets. Call once at start."""
    settings = settings or _LoggingSettings()
    logging.getLogger("roma.events").disabled = False
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    redaction = RedactionFilter(_secret_values(settings))
    handler.addFilter(redaction)
    handler.addFilter(LeadTokenFilter())
    handler.addFilter(CallSidFilter())
    # uvicorn's CLI has already run its own dictConfig by the time the app factory calls
    # this, so these stick. Both filters: uvicorn.access logs URLs, and a URL is exactly
    # where a credential ends up.
    _attach_to_uvicorn(redaction, LeadTokenFilter())
    for name in _UVICORN_LOGGERS:
        for existing_handler in logging.getLogger(name).handlers:
            existing_handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=settings.log_level.upper(),
        handlers=[handler],
        force=True,
    )
    if redaction.skipped_short:
        logging.getLogger("roma").warning(
            "%d configured secret(s) are shorter than %d chars and are NOT redacted from "
            "logs. Real credentials are longer; check for a placeholder in the environment.",
            redaction.skipped_short,
            _MIN_SECRET_LEN,
        )


class LogEvent(StrEnum):
    """Reviewed operational vocabulary; never derive an event from caller text."""

    CHECKPOINT_COMPLETED = "checkpoint_completed"
    CHECKPOINT_FAILED = "checkpoint_failed"
    STATE_CACHE_FAILED = "state_cache_failed"
    STREAM_FALLBACK = "stream_fallback"
    QUEUE_SUMMARY = "queue_summary"
    TURN_INTERRUPTED = "turn_interrupted"
    CALL_DISCONNECTED = "call_disconnected"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"
    TURN_STARTED = "turn_started"
    ASR_COMPLETED = "asr_completed"
    CONTROLLER_COMPLETED = "controller_completed"
    LLM_COMPLETED = "llm_completed"
    LLM_FAILED = "llm_failed"
    SAFETY_COMPLETED = "safety_completed"
    TTS_COMPLETED = "tts_completed"
    PLAYBACK_COMPLETED = "playback_completed"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"


_log_context: ContextVar[dict | None] = ContextVar("structured_log_context", default=None)
_CONTEXT_FIELDS = ("correlation_id", "request_id", "call_id", "turn_id", "stage", "language")
_STAGES = {"open", "discover", "value", "structure", "pivot", "objection", "close"}
_LANGUAGES = {"gu", "hi", "en", "gu-en", "hi-en", "gujarati", "hindi", "english",
              "gujarati_english", "hindi_english"}
_ERROR_CODES = {code.value for code in VoiceErrorCode}
_ID = re.compile(r"(?:CALL-[0-9]+|[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z")
_CALL_ID = re.compile(r"(?:CA[A-Za-z0-9_]{1,64}|CALL-[0-9]+|voice-lab-[A-Za-z0-9-]{1,64})\Z")


def _context_value(key, value):
    if key == "turn_id":
        return value if type(value) is int and 1 <= value <= 1000000 else None
    if not isinstance(value, str):
        return None
    if key == "stage":
        return value if value in _STAGES else None
    if key == "language":
        value = value.lower()
        return {"gujarati": "gu", "hindi": "hi", "english": "en",
                "gujarati_english": "gu-en", "hindi_english": "hi-en"}.get(value, value) if value in _LANGUAGES else None
    pattern = _CALL_ID if key == "call_id" else _ID
    return value if pattern.fullmatch(value) else None


def update_log_context(**values):
    """Update only reviewed metadata; enclosing log_context restores the prior scope."""
    context = dict(_log_context.get() or {})
    for key, value in values.items():
        if key in _CONTEXT_FIELDS:
            context[key] = _context_value(key, value)
    _log_context.set(context)


@contextmanager
def log_context(**values):
    """Task-local correlation, restored even on cancellation or a nested failure."""
    token = _log_context.set(dict(_log_context.get() or {}))
    sid_token = current_call_sid.set(current_call_sid.get())
    try:
        update_log_context(**values)
        if "call_id" in values:
            current_call_sid.set((_log_context.get() or {}).get("call_id"))
        yield
    finally:
        current_call_sid.reset(sid_token)
        _log_context.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per record. Arbitrary messages/extras/tracebacks are never emitted.

    Old module loggers remain usable and correlated as legacy_log. Their free-form
    messages can include transcripts; structured events replace those diagnostics.
    Redaction filters remain installed for handlers outside this JSON boundary.
    """

    def format(self, record):
        context = getattr(record, "roma_context", (_log_context.get() or {}))
        row = {"timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
               "level": record.levelname}
        for key in _CONTEXT_FIELDS:
            value = context.get(key)
            if key == "call_id" and value is None:
                value = current_call_sid.get()
            row[key] = _context_value(key, value)
        event = getattr(record, "event", None)
        row["event"] = event.value if isinstance(event, LogEvent) else "legacy_log"
        latency = getattr(record, "latency_ms", None)
        row["latency_ms"] = latency if type(latency) in (int, float) and math.isfinite(latency) and latency >= 0 else None
        code = getattr(record, "error_code", None)
        if isinstance(code, str) and code in _ERROR_CODES:
            row["error_code"] = code
        status = getattr(record, "status", None)
        if isinstance(status, str) and status in ("played", "failed", "received", "interrupted", "disconnected"):
            row["status"] = status
        status_code = getattr(record, "status_code", None)
        if type(status_code) is int and 100 <= status_code <= 599:
            row["status_code"] = status_code
        queue_name = getattr(record, "queue_name", None)
        checkpoint_metrics = getattr(record, "checkpoint_metrics", None)
        if isinstance(checkpoint_metrics, dict):
            row["checkpoint"] = {key: value for key, value in checkpoint_metrics.items()
                                 if key in {"checkpoints", "failures", "retries", "redis_failures", "recoveries", "pending", "lag_ms"}
                                 and type(value) in (int, float) and math.isfinite(value) and value >= 0}
        queue_metrics = getattr(record, "queue_metrics", None)
        if isinstance(queue_name, str) and queue_name in {"audio_in", "partials", "finals", "tokens", "chunks", "safe_chunks", "tts_text", "tts_audio"} and isinstance(queue_metrics, dict):
            row["queue_name"] = queue_name
            row["queue"] = {key: value for key, value in queue_metrics.items()
                            if key in {"capacity", "peak_depth", "depth", "dropped_oldest", "dropped_newest", "discarded", "high_water_hits", "wait_ms", "put_wait_ms", "get_wait_ms"}
                            and type(value) in (int, float) and math.isfinite(value) and value >= 0}
        return json.dumps(row, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def log_event(event: LogEvent, *, latency_ms=None, error_code=None, status=None, status_code=None, queue_name=None, queue_metrics=None, checkpoint_metrics=None):
    if not isinstance(event, LogEvent):
        raise ValueError("Use a reviewed LogEvent")
    failed = event in {LogEvent.CHECKPOINT_FAILED, LogEvent.STATE_CACHE_FAILED, LogEvent.REQUEST_FAILED, LogEvent.TURN_FAILED, LogEvent.LLM_FAILED}
    logging.getLogger("roma.events").log(
        logging.ERROR if failed else logging.INFO, event.value,
        extra={"event": event, "roma_context": dict(_log_context.get() or {}),
               "latency_ms": latency_ms, "error_code": error_code,
               "status": status, "status_code": status_code,
               "queue_name": queue_name, "queue_metrics": queue_metrics, "checkpoint_metrics": checkpoint_metrics},
    )


class RequestLogMiddleware:
    """Pure ASGI middleware: streaming/WS safe, no body, query, path or auth inspection.

    Server-generated IDs avoid reflecting client PII. The request correlation is
    inherited by child tasks; voice calls also have their own stable correlation.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        request_id, correlation_id = uuid4().hex, uuid4().hex
        status = None
        started = perf_counter()
        async def correlated_send(message):
            nonlocal status
            if message["type"] in ("http.response.start", "websocket.accept"):
                message = dict(message)
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in (b"x-request-id", b"x-correlation-id")]
                headers.extend([(b"x-request-id", request_id.encode()),
                                (b"x-correlation-id", correlation_id.encode())])
                message["headers"] = headers
                status = message.get("status", 101)
            await send(message)
        with log_context(request_id=request_id, correlation_id=correlation_id):
            try:
                await self.app(scope, receive, correlated_send)
            except BaseException as exc:
                log_event(LogEvent.REQUEST_FAILED, latency_ms=(perf_counter() - started) * 1000,
                          error_code=VoiceErrorCode.CANCELLED.value
                          if isinstance(exc, asyncio.CancelledError) else None,
                          status_code=500 if status is None else status)
                raise
            else:
                log_event(LogEvent.REQUEST_COMPLETED, latency_ms=(perf_counter() - started) * 1000,
                          status_code=status)
