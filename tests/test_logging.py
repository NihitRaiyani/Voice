import logging
import sys

from roma.logging_setup import RedactionFilter


def test_redaction_filter_scrubs_secret_in_args():
    secret = "vobiz-auth-abcdef"
    filt = RedactionFilter([secret])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling vobiz with token %s",
        args=(secret,),
        exc_info=None,
    )
    assert filt.filter(record) is True
    message = record.getMessage()
    assert secret not in message
    assert "***REDACTED***" in message


def test_redaction_filter_ignores_empty_secrets():
    filt = RedactionFilter(["", None])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="nothing secret here",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
    assert record.getMessage() == "nothing secret here"


def test_redaction_filter_scrubs_secret_in_traceback():
    secret = "redis://user:s3cr3t-pw@host:6379"
    filt = RedactionFilter([secret])
    try:
        raise ValueError(f"connection failed for {secret}")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="db error",
        args=(),
        exc_info=exc_info,
    )
    assert filt.filter(record) is True
    assert record.exc_text is not None
    assert secret not in record.exc_text
    assert "***REDACTED***" in record.exc_text
