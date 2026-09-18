import logging
import sys

from roma.core.config import Settings
from roma.core.logging import RedactionFilter, configure_logging


def test_redaction_filter_scrubs_secret_in_args():
    secret = "0123456789abcdef0123456789abcdef"
    filt = RedactionFilter([secret])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling Twilio with token %s",
        args=(secret,),
        exc_info=None,
    )
    assert filt.filter(record) is True
    message = record.getMessage()
    assert secret not in message
    assert "***REDACTED***" in message


def test_configured_logging_redacts_all_twilio_credentials(capsys):
    account_sid = "test-twilio-account-sid"
    auth_token = "0123456789abcdef0123456789abcdef"
    from_number = "+919876543210"
    settings = Settings(
        _env_file=None,
        sarvam_api_key="test-sarvam-api-key",
        openai_api_key="test-openai-api-key",
        redis_url="redis://localhost:6379/0",
        twilio_account_sid=account_sid,
        twilio_auth_token=auth_token,
        twilio_from_number=from_number,
    )

    configure_logging(settings)
    logging.getLogger("test").warning(
        "Twilio account=%s token=%s caller=%s",
        account_sid,
        auth_token,
        from_number,
    )

    output = capsys.readouterr().err
    assert account_sid not in output
    assert auth_token not in output
    assert from_number not in output
    assert output.count("***REDACTED***") == 3


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
