"""Logging setup with secret redaction (docs/07: no secret in logs).

RedactionFilter is defense-in-depth: even if a secret value is accidentally
logged, it is scrubbed from the record before emission.
"""

import logging
import re
from contextvars import ContextVar

from roma.config import Settings, get_settings

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
    voice. `roma.telephony` never logs it — but `uvicorn.access` logs the whole request
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


def _secret_values(settings: Settings):
    """Every value that must never appear in a log line (CLAUDE.md gate 2, docs/07)."""
    return [
        settings.twilio_account_sid.get_secret_value(),
        settings.twilio_auth_token.get_secret_value(),
        settings.twilio_from_number.get_secret_value(),
        settings.sarvam_api_key.get_secret_value(),
        settings.openai_api_key.get_secret_value(),
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
    settings = settings or get_settings()
    handler = logging.StreamHandler()
    redaction = RedactionFilter(_secret_values(settings))
    handler.addFilter(redaction)
    handler.addFilter(LeadTokenFilter())
    handler.addFilter(CallSidFilter())
    # uvicorn's CLI has already run its own dictConfig by the time the app factory calls
    # this, so these stick. Both filters: uvicorn.access logs URLs, and a URL is exactly
    # where a credential ends up.
    _attach_to_uvicorn(redaction, LeadTokenFilter())
    logging.basicConfig(
        level=settings.log_level.upper(),
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s [%(call_sid)s] %(message)s",
        force=True,
    )
    if redaction.skipped_short:
        logging.getLogger("roma").warning(
            "%d configured secret(s) are shorter than %d chars and are NOT redacted from "
            "logs. Real credentials are longer; check for a placeholder in the environment.",
            redaction.skipped_short,
            _MIN_SECRET_LEN,
        )
