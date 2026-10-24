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
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
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

    try:
        configure_logging(settings)
        logging.getLogger("test").warning(
            "Twilio account=%s token=%s caller=%s",
            account_sid,
            auth_token,
            from_number,
        )
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)

    output = capsys.readouterr().err
    assert account_sid not in output
    assert auth_token not in output
    assert from_number not in output
    # Section 21 omits unreviewed free-form messages entirely, beyond redaction.
    import json
    row = json.loads(output.splitlines()[-1])
    assert row["event"] == "legacy_log"
    assert "message" not in row


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


def test_jwt_signing_secret_is_redacted_in_records_for_both_configuration_paths(monkeypatch):
    from roma.core.logging import _LoggingSettings

    secret = 'synthetic-jwt-signing-key-for-redaction-only'
    monkeypatch.setenv('AUTH_JWT_SECRET', secret)
    monkeypatch.setitem(_LoggingSettings.model_config, 'env_file', None)
    explicit = Settings(
        _env_file=None,
        sarvam_api_key='synthetic-sarvam-key',
        openai_api_key='synthetic-openai-key',
        redis_url='redis://unused',
    )
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        for settings in (None, explicit):
            configure_logging(settings)
            try:
                raise RuntimeError(f'synthetic failure involving {secret}')
            except RuntimeError:
                exc_info = sys.exc_info()
            record = logging.LogRecord('test', logging.ERROR, __file__, 1,
                                       'JWT signing key=%s', (secret,), exc_info)
            # Inspect actual record mutation before any JSON formatter can omit text.
            for handler in root.handlers:
                for filter_ in handler.filters:
                    assert filter_.filter(record)
            assert secret not in record.getMessage()
            assert '***REDACTED***' in record.getMessage()
            assert record.exc_text is not None and secret not in record.exc_text
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
