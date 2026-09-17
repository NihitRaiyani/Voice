"""The per-call lead token must never reach disk (docs/07).

`?lead=<token>` dereferences in Redis to a real lead's name, city and segment, and it
authorises the `/ws` socket carrying their live voice. It is minted per call, so
RedactionFilter — which scrubs a fixed list of configured values — cannot see it.
"""

import logging

from roma.dialer.trigger import build_answer_url
from roma.logging_setup import (
    LeadTokenFilter,
    RedactionFilter,
    configure_logging,
)


def _record(msg, args=()):
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def _filtered(msg, args=()):
    record = _record(msg, args)
    assert LeadTokenFilter().filter(record) is True
    return record.getMessage()


def test_the_uvicorn_access_line_does_not_carry_the_token():
    """The exact shape uvicorn.access emits, which is how this leaked in the first place."""
    token = "Xq7bL2n-mK4pR8sT1vW3yZ_aB5cD6eF0"
    message = _filtered(
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:52000", "POST", f"/answer?lead={token}", "1.1", 200),
    )
    assert token not in message
    assert "lead=***REDACTED***" in message


def test_a_token_minted_the_real_way_is_scrubbed():
    """Pinned against the real minter, not a hand-written token, so an alphabet change
    to `secrets.token_urlsafe` cannot silently walk out of the regex."""
    url = build_answer_url(
        "https://example.trycloudflare.com", "aB3-_xY9zQ7wE1rT5yU8iO0pA2sD4fG6"
    )
    message = _filtered("dialling %s", (url,))
    assert "aB3-_xY9zQ7wE1rT5yU8iO0pA2sD4fG6" not in message
    assert "lead=***REDACTED***" in message


def test_the_stream_url_form_is_scrubbed_too():
    """`/ws?t=<stream>&lead=<token>` — the token rides an `&`, not a `?`."""
    token = "s3cr3tLeadToken_abcdefghijklmnop"
    message = _filtered("opening %s", (f"wss://host/ws?t=streamtok&lead={token}",))
    assert token not in message
    assert "t=streamtok" in message, "only the lead token goes; the stream token is not PII"


def test_a_percent_encoded_token_is_scrubbed():
    message = _filtered("url=%s", ("/answer?lead=ab%2Fcd%3Def",))
    assert "ab%2Fcd" not in message
    assert "lead=***REDACTED***" in message


def test_an_ordinary_line_is_left_exactly_alone():
    """The filter runs on every record in the process; it must not rewrite prose."""
    assert _filtered("answer: streaming call_id=%s lead=%s", ("abc", "yes")) == (
        "answer: streaming call_id=abc lead=yes"
    )


def test_a_word_ending_in_lead_is_not_a_query_param():
    assert _filtered("overlead=7 and downlead=9") == "overlead=7 and downlead=9"


def test_the_filter_is_installed_on_the_configured_handler():
    """A filter no one installs is the fourth inert feature in this repo."""
    configure_logging()
    handlers = logging.getLogger().handlers
    assert any(any(isinstance(f, LeadTokenFilter) for f in h.filters) for h in handlers), (
        "configure_logging() must install LeadTokenFilter, or uvicorn.access leaks the token"
    )


def test_the_filter_is_installed_on_uvicorns_own_loggers():
    """THE bug this file exists for.

    The root handler is not enough. uvicorn's CLI — the documented entrypoint — configures
    `uvicorn.access` with its own handler and `propagate: False`, so a root-handler filter
    never sees one of its records. A live canary probe leaked the token with the filter
    installed on the root handler and nowhere else.
    """
    configure_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        filters = logging.getLogger(name).filters
        assert any(isinstance(f, LeadTokenFilter) for f in filters), (
            f"{name} bypasses the root handler; the filter must be on the logger itself"
        )
        assert any(isinstance(f, RedactionFilter) for f in filters), (
            f"{name} logs URLs, and a URL is where a credential ends up"
        )


def test_configure_logging_twice_does_not_stack_filters():
    configure_logging()
    before = len(logging.getLogger("uvicorn.access").filters)
    configure_logging()
    configure_logging()
    assert len(logging.getLogger("uvicorn.access").filters) == before


def test_the_scrub_preserves_the_arity_uvicorns_formatter_unpacks():
    """`AccessFormatter.formatMessage` does `(a, b, c, d, e) = record.args`.

    Collapsing args to `()` after scrubbing — the obvious implementation — turns the
    redaction into a ValueError inside the logging machinery on every single request.
    """
    record = _record(
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:52000", "GET", "/answer?lead=Tok3n_abcdefghijklmnop", "1.1", 200),
    )
    LeadTokenFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert len(record.args) == 5, "uvicorn unpacks exactly five"
    assert record.args[4] == 200, "the status code must survive as an int, not a string"


def test_uvicorns_real_access_formatter_still_renders_the_scrubbed_record():
    """End-to-end against the actual formatter, not our idea of it."""
    from uvicorn.logging import AccessFormatter

    record = _record(
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:52000", "POST", "/answer?lead=Tok3n_abcdefghijklmnop", "1.1", 200),
    )
    LeadTokenFilter().filter(record)
    rendered = AccessFormatter(use_colors=False).format(record)
    assert "Tok3n_abcdefghijklmnop" not in rendered
    assert "lead=***REDACTED***" in rendered
    assert "POST" in rendered and "200" in rendered


def test_the_redaction_filter_also_survives_the_access_record_shape():
    """RedactionFilter now shares the arity-preserving scrub; pin that it did not
    regress the five-tuple while gaining uvicorn coverage."""
    secret = "vobiz-auth-abcdef123456"
    record = _record(
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:52000", "GET", f"/answer?token={secret}", "1.1", 200),
    )
    RedactionFilter([secret]).filter(record)
    assert len(record.args) == 5
    assert secret not in record.getMessage()
